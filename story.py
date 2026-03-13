"""Интерактивные истории — движок и хендлеры."""
import logging
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import ai as AI
from database import Story, User
from keyboards import story_genre_kb, story_choices_kb, GENRES

log = logging.getLogger(__name__)
router = Router()


def _parse(text: str) -> tuple[str, list[str]]:
    """Разбить ответ AI на текст и варианты выбора."""
    parts = re.split(r"\n\s*\d+\)\s*", text, maxsplit=4)
    if len(parts) >= 4:
        return parts[0].strip(), [c.strip() for c in parts[1:4]]
    # Fallback
    return text.strip(), ["Идти вперёд", "Осмотреться", "Ждать"]


async def _active_story(session: AsyncSession, user_id: int) -> Story | None:
    r = await session.execute(select(Story).where(Story.user_id == user_id, Story.status == "active"))
    return r.scalar_one_or_none()


@router.message(F.text == "📖 История")
@router.message(Command("story"))
async def cmd_story(message: Message, session: AsyncSession, user: User) -> None:
    active = await _active_story(session, user.telegram_id)
    if active:
        h = active.get_history()
        if h:
            last = h[-1]
            choices = last.get("choices", ["Продолжить", "Осмотреться", "Отступить"])
            genre_label = GENRES.get(active.genre, active.genre)
            await message.answer(
                f"📖 <b>{genre_label}</b> — история продолжается\n\n{last['text']}",
                reply_markup=story_choices_kb(choices),
            )
            return
    await message.answer("📖 <b>Интерактивные истории</b>\n\nВыбери жанр:", reply_markup=story_genre_kb())


@router.callback_query(F.data.startswith("sg:"))
async def cb_genre(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    genre = callback.data.split(":")[1]
    if genre not in GENRES:
        await callback.answer("Неизвестный жанр."); return

    # Close old story
    old = await _active_story(session, user.telegram_id)
    if old: old.status = "finished"

    label = GENRES[genre]
    await callback.message.edit_text(f"⏳ Начинаю <b>{label}</b>...", parse_mode="HTML")
    await callback.answer()

    try:
        raw = await AI.story_gen(genre, [])
        text, choices = _parse(raw)
    except Exception as e:
        log.error("Story start error: %s", e)
        await callback.message.edit_text("❌ Ошибка генерации. Попробуй /story")
        return

    story = Story(user_id=user.telegram_id, genre=genre)
    story.set_history([{"text": text, "choices": choices, "choice": None}])
    session.add(story)
    await session.commit()

    formatted = text + "\n\n" + "\n".join(f"{i}) {c}" for i, c in enumerate(choices, 1))
    await callback.message.edit_text(
        f"📖 <b>{label}</b>\n\n{formatted}",
        reply_markup=story_choices_kb(choices),
    )


@router.callback_query(F.data.startswith("sc:"))
async def cb_choice(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    idx = int(callback.data.split(":")[1]) - 1
    story = await _active_story(session, user.telegram_id)
    if not story:
        await callback.answer("Нет активной истории. /story"); return

    history = story.get_history()
    if not history:
        await callback.answer("История повреждена."); return

    last = history[-1]
    choices = last.get("choices", [])
    if idx >= len(choices):
        await callback.answer("Неверный выбор."); return

    chosen = choices[idx]
    last["choice"] = chosen

    await callback.message.edit_text(
        f"📖 <i>Выбор: {chosen}</i>\n\n⏳ Генерирую...",
        parse_mode="HTML",
    )
    await callback.answer()

    try:
        raw = await AI.story_gen(story.genre, history, chosen)
        text, new_choices = _parse(raw)
    except Exception as e:
        log.error("Story continue error: %s", e)
        await callback.message.edit_text("❌ Ошибка. Попробуй ещё раз /story")
        return

    history.append({"text": text, "choices": new_choices, "choice": None})
    story.set_history(history)
    await session.commit()

    label = GENRES.get(story.genre, story.genre)
    formatted = text + "\n\n" + "\n".join(f"{i}) {c}" for i, c in enumerate(new_choices, 1))
    await callback.message.edit_text(
        f"📖 <b>{label}</b>\n\n{formatted}",
        reply_markup=story_choices_kb(new_choices),
    )


@router.callback_query(F.data == "send")
async def cb_story_end(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    story = await _active_story(session, user.telegram_id)
    if story:
        story.status = "finished"
        await session.commit()
    await callback.message.edit_text("📖 История завершена. Новая: /story")
    await callback.answer()
