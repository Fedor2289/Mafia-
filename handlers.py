"""
Общие хендлеры:
  /start /help /settings /admin /cancel
  AI чат (личка и группы)
  Панель администратора
"""
import logging
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatType
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

import ai as AI
from config import settings, report_error
from database import User, MafiaGame, Story, Session
from keyboards import main_kb, settings_kb, admin_kb, back_kb, confirm_kb, cancel_kb

log = logging.getLogger(__name__)
router = Router()

LOG_ERR = "logs/errors.log"
LOG_ALL = "logs/bot.log"


# ── FSM States ─────────────────────────────────────────────────────────────────

class Adm(StatesGroup):
    imp_user   = State()
    imp_text   = State()
    ai_name    = State()
    ai_text    = State()
    bcast_text = State()
    bcast_ok   = State()
    toggle_adm = State()


# ── /start /help ───────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(
        f"Ну что, {user.first_name}. Явился.\n\n"
        "Я могу:\n"
        "• Отвечать на вопросы (неохотно)\n"
        "• Вести игру «Мафия» в группах\n"
        "• Рассказывать интерактивные истории\n\n"
        "Давай, говори — чего хочешь.",
        reply_markup=main_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📋 <b>Команды:</b>\n\n"
        "💬 Просто напиши — получишь ответ (в личке всегда, в группе тоже)\n"
        "/clear — сбросить историю разговора\n\n"
        "🎮 <b>Мафия</b> (в группе)\n"
        "/mafia — создать игру\n"
        "/endmafia — завершить игру (админ)\n\n"
        "📖 /story — интерактивная история\n\n"
        "⚙️ /settings — настройки\n"
        "👑 /admin — панель администратора"
    )


# ── Settings ───────────────────────────────────────────────────────────────────

@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User) -> None:
    await message.answer(
        f"⚙️ <b>Настройки</b>\n\nAI: <b>{user.provider}</b>",
        reply_markup=settings_kb(user.provider, user.group_ai),
    )


@router.callback_query(F.data.startswith("set_prov:"))
async def cb_set_provider(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.provider = callback.data.split(":")[1]
    await session.commit()
    await callback.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\nAI: <b>{user.provider}</b>",
        reply_markup=settings_kb(user.provider, user.group_ai),
    )
    await callback.answer("✅ Сохранено")


@router.callback_query(F.data == "toggle_group_ai")
async def cb_toggle_group(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.group_ai = not user.group_ai
    await session.commit()
    status = "включены ✅" if user.group_ai else "выключены ☐"
    await callback.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\nAI: <b>{user.provider}</b>",
        reply_markup=settings_kb(user.provider, user.group_ai),
    )
    await callback.answer(f"Ответы в группах {status}")


@router.callback_query(F.data == "clear_history")
async def cb_clear(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.clear_history()
    await session.commit()
    await callback.answer("🗑 История очищена", show_alert=True)


@router.message(Command("clear"))
async def cmd_clear(message: Message, session: AsyncSession, user: User) -> None:
    user.clear_history()
    await session.commit()
    await message.answer("🗑 История разговора сброшена.")


# ── AI chat — private ─────────────────────────────────────────────────────────

@router.message(F.text == "🤖 AI чат", F.chat.type == ChatType.PRIVATE)
async def btn_ai_chat(message: Message) -> None:
    await message.answer("Слушаю. Пиши — отвечу. Или не отвечу. Посмотрим.")


@router.message(F.text, F.chat.type == ChatType.PRIVATE)
async def pm_message(message: Message, session: AsyncSession, user: User) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    history = user.get_history()

    try:
        reply = await AI.chat(text, history, user.provider)
    except Exception as e:
        await report_error(e, ctx="pm_message", user_id=user.telegram_id)
        reply = "Что-то сломалось. Попробуй позже."

    user.add_message("user", text)
    user.add_message("assistant", reply)
    await session.commit()
    await message.answer(reply)


# ── AI chat — groups ──────────────────────────────────────────────────────────

@router.message(F.text, F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group_message(message: Message, session: AsyncSession, user: User) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return

    bot_info = await message.bot.get_me()
    bot_id   = bot_info.id
    bot_name = bot_info.username or ""

    is_mention = f"@{bot_name}" in text
    is_reply   = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == bot_id
    )
    # group_ai — отвечать на все сообщения в группе (настройка пользователя)
    respond = is_mention or is_reply or (settings.GROUP_AI_ALL or user.group_ai)

    if not respond:
        return

    clean = text.replace(f"@{bot_name}", "").strip()
    if not clean:
        await message.reply("Ну и? Скажи хоть что-нибудь.")
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    try:
        reply = await AI.chat(clean, [], user.provider)
    except Exception as e:
        await report_error(e, ctx="group_message", user_id=user.telegram_id)
        reply = "Ошибка. Как неожиданно."

    await message.reply(reply)


# ── /cancel ────────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
        await message.answer("❌ Отменено.")
    else:
        await message.answer("Нечего отменять.")


@router.callback_query(F.data == "adm:cancel")
async def cb_adm_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Отменено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ В панель", callback_data="adm:back")
        ]])
    )
    await callback.answer()


# ── Admin panel ────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, user: User) -> None:
    if not user.is_admin:
        await message.answer("⛔ Нет доступа."); return
    await message.answer(
        f"👑 <b>Панель администратора</b>\nID: <code>{user.telegram_id}</code>",
        reply_markup=admin_kb(),
    )


@router.callback_query(F.data == "adm:back")
async def cb_adm_back(callback: CallbackQuery, user: User) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        f"👑 <b>Панель администратора</b>\nID: <code>{user.telegram_id}</code>",
        reply_markup=admin_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:close")
async def cb_adm_close(callback: CallbackQuery) -> None:
    await callback.message.delete()
    await callback.answer()


# Stats

@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return

    users   = (await session.execute(select(func.count()).select_from(User))).scalar()
    games   = (await session.execute(select(func.count()).select_from(MafiaGame))).scalar()
    active  = (await session.execute(
        select(func.count()).select_from(MafiaGame).where(MafiaGame.status == "playing"))).scalar()
    stories = (await session.execute(select(func.count()).select_from(Story))).scalar()
    admins  = (await session.execute(
        select(func.count()).select_from(User).where(User.is_admin == True))).scalar()

    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>  (👑 админов: {admins})\n"
        f"🎮 Игр Мафия: <b>{games}</b>  (🟢 активных: {active})\n"
        f"📖 Историй: <b>{stories}</b>\n\n"
        f"📁 errors.log: {_lines(LOG_ERR)} строк\n"
        f"📁 bot.log: {_lines(LOG_ALL)} строк",
        reply_markup=back_kb(),
    )
    await callback.answer()


# Log viewers

@router.callback_query(F.data == "adm:errlog")
async def cb_errlog(callback: CallbackQuery, user: User) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        f"🐛 <b>Последние ошибки</b>\n\n<pre>{_tail(LOG_ERR)}</pre>",
        reply_markup=back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:alllog")
async def cb_alllog(callback: CallbackQuery, user: User) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        f"📋 <b>Общий лог</b>\n\n<pre>{_tail(LOG_ALL)}</pre>",
        reply_markup=back_kb(),
    )
    await callback.answer()


# Impersonate

@router.callback_query(F.data == "adm:imp")
async def cb_imp_start(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        "📢 Введи Telegram ID или @username пользователя:", reply_markup=cancel_kb()
    )
    await state.set_state(Adm.imp_user)
    await callback.answer()


@router.message(Adm.imp_user)
async def adm_imp_user(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = message.text.strip().lstrip("@")
    if raw.lstrip("-").isdigit():
        r = await session.execute(select(User).where(User.telegram_id == int(raw)))
    else:
        r = await session.execute(select(User).where(User.username == raw))
    target = r.scalar_one_or_none()
    if not target:
        await message.answer("❌ Пользователь не найден. Попробуй ещё."); return
    await state.update_data(tg_id=target.telegram_id, name=target.first_name)
    await state.set_state(Adm.imp_text)
    await message.answer(
        f"✅ <b>{target.first_name}</b> ({target.telegram_id})\n\nВведи текст сообщения:",
        reply_markup=cancel_kb(),
    )


@router.message(Adm.imp_text)
async def adm_imp_send(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    await state.clear()
    try:
        await message.bot.send_message(d["tg_id"], message.text or "")
        await message.answer(f"✅ Отправлено → <b>{d['name']}</b>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>")


# AI Character

@router.callback_query(F.data == "adm:ai_char")
async def cb_ai_char(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        "🎭 Введи имя и роль персонажа:\n<i>Пример: Виктор — детектив 30-х, циничный</i>",
        reply_markup=cancel_kb(),
    )
    await state.set_state(Adm.ai_name)
    await callback.answer()


@router.message(Adm.ai_name)
async def adm_ai_name(message: Message, state: FSMContext) -> None:
    await state.update_data(character=message.text.strip())
    await state.set_state(Adm.ai_text)
    await message.answer("Введи запрос для этого персонажа:", reply_markup=cancel_kb())


@router.message(Adm.ai_text)
async def adm_ai_text(message: Message, state: FSMContext, user: User) -> None:
    d = await state.get_data()
    await state.clear()
    await message.bot.send_chat_action(message.chat.id, "typing")
    from ai import AIProvider, get_provider, AIMessage  # type: ignore
    provider = get_provider(user.provider)
    try:
        reply = await provider.ask(
            [{"role": "user", "content": message.text or ""}],
            system=f"Ты — {d['character']}. Отвечай от его лица, коротко и в характере.",
            temperature=0.9,
        )
        await message.answer(f"🎭 <b>{d['character']}:</b>\n\n{reply}")
    except Exception as e:
        await message.answer(f"❌ Ошибка AI: <code>{e}</code>")


# Broadcast

@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        "📣 Введи текст рассылки (HTML поддерживается):", reply_markup=cancel_kb()
    )
    await state.set_state(Adm.bcast_text)
    await callback.answer()


@router.message(Adm.bcast_text)
async def adm_bcast_preview(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    await state.update_data(text=text)
    await state.set_state(Adm.bcast_ok)
    await message.answer(
        f"📋 <b>Предпросмотр:</b>\n\n{text}\n\nОтправить всем?",
        reply_markup=confirm_kb("adm:bcast_go"),
    )


@router.callback_query(F.data == "adm:bcast_go")
async def cb_bcast_go(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    d = await state.get_data()
    text = d.get("text", "")
    await state.clear()
    await callback.message.edit_text("⏳ Рассылка...")
    await callback.answer()

    sent = failed = 0
    async with Session() as session:
        r = await session.execute(select(User.telegram_id))
        ids = [row[0] for row in r.all()]

    for uid in ids:
        try:
            await callback.bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"✅ Рассылка завершена\n\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}",
        reply_markup=back_kb(),
    )


# Reset games

@router.callback_query(F.data == "adm:reset")
async def cb_reset(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    r = await session.execute(
        select(MafiaGame).where(MafiaGame.chat_id == callback.message.chat.id,
                                MafiaGame.status != "finished")
    )
    games = r.scalars().all()
    for g in games:
        g.status = "finished"
        g.finished_at = datetime.utcnow()
    await session.commit()
    await callback.message.edit_text(
        f"✅ Сброшено {len(games)} игр в этом чате.", reply_markup=back_kb()
    )
    await callback.answer()


# Manage admins

@router.callback_query(F.data == "adm:admins")
async def cb_admins(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.is_admin:
        await callback.answer("⛔", show_alert=True); return
    await callback.message.edit_text(
        "👑 Введи Telegram ID пользователя.\n"
        "Если не админ — станет им. Если уже — будет разжалован.",
        reply_markup=cancel_kb(),
    )
    await state.set_state(Adm.toggle_adm)
    await callback.answer()


@router.message(Adm.toggle_adm)
async def adm_toggle(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    raw = message.text.strip()
    await state.clear()
    if not raw.lstrip("-").isdigit():
        await message.answer("❌ Нужен числовой ID."); return
    tid = int(raw)
    if tid == user.telegram_id:
        await message.answer("❌ Нельзя менять свои права."); return
    r = await session.execute(select(User).where(User.telegram_id == tid))
    target = r.scalar_one_or_none()
    if not target:
        await message.answer("❌ Пользователь не найден в БД."); return
    target.is_admin = not target.is_admin
    await session.commit()
    act = "назначен администратором ✅" if target.is_admin else "снят с должности ❌"
    await message.answer(f"<b>{target.first_name}</b> ({tid}) {act}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tail(path: str, n: int = 35) -> str:
    if not os.path.exists(path):
        return "Файл не найден."
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        raw = "".join(lines[-n:]).strip()
        raw = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return raw[-3000:] if len(raw) > 3000 else raw
    except Exception as e:
        return f"Ошибка: {e}"


def _lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        return sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
    except Exception:
        return -1
