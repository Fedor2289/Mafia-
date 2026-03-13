"""
Полноценная игра «Мафия».

Роли:
  Мирные: Мирный житель, Детектив (проверяет роль), Доктор (лечит)
  Мафия:  Мафиози, Дон (видит команду, убивает)

Фазы:
  waiting → day_talk → voting → night → day_talk → ...

AI игроки:
  • Участвуют в обсуждении
  • Голосуют осознанно (мафия голосует за мирных, мирные за подозреваемых)
  • Выполняют ночные действия
  • Имеют память подозрений
"""
import asyncio
import logging
import random
from datetime import datetime
from typing import Any

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.enums import ChatType
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import ai as AI
from config import settings, report_error
from database import MafiaGame, Session, User

log = logging.getLogger(__name__)
router = Router()

# ── Timing (seconds) ──────────────────────────────────────────────────────────
T_LOBBY    = 60    # ожидание игроков
T_DISCUSS  = 90    # дневное обсуждение
T_VOTE     = 40    # голосование
T_NIGHT    = 35    # ночь

# ── Роли ──────────────────────────────────────────────────────────────────────
ROLES = {
    "civilian": dict(name="Мирный житель", emoji="👤", team="town", night=False),
    "detective": dict(name="Детектив",     emoji="🔍", team="town", night=True,  action="check"),
    "doctor":    dict(name="Доктор",       emoji="💊", team="town", night=True,  action="heal"),
    "mafia":     dict(name="Мафия",        emoji="🔫", team="mafia", night=True, action="kill"),
    "don":       dict(name="Дон мафии",    emoji="👑", team="mafia", night=True, action="kill"),
}

AI_NAMES = ["Артём","Карина","Максим","Ольга","Дмитрий","Светлана",
            "Иван","Татьяна","Алексей","Наталья","Виктор","Елена"]


def _role_distribution(n: int) -> list[str]:
    """Балансированное распределение ролей."""
    if n < 4: n = 4
    mafia_n = max(1, n // 3)
    roles: list[str] = []
    if n >= 6:
        roles.append("don"); mafia_n -= 1
    roles += ["mafia"] * mafia_n
    if n >= 5: roles.append("detective")
    if n >= 7: roles.append("doctor")
    roles += ["civilian"] * (n - len(roles))
    random.shuffle(roles)
    return roles


# ── Структура состояния игры ───────────────────────────────────────────────────
# state = {
#   "phase": "waiting|discuss|voting|night|finished",
#   "day": int,
#   "players": [
#     {"id": "u_123" | "ai_0", "name": str, "role": str, "alive": bool,
#      "is_ai": bool, "votes": int, "night_target": str|None,
#      "suspicions": {name: score}, "protected": bool}
#   ],
#   "log": [str],            # история чата
#   "protected_id": str|None,
#   "winner": str|None,
#   "host_msg_id": int|None, # message_id главного сообщения ведущего
# }

def _empty_state() -> dict:
    return {"phase": "waiting", "day": 0, "players": [], "log": [],
            "protected_id": None, "winner": None, "host_msg_id": None}


def _alive(state: dict) -> list[dict]:
    return [p for p in state["players"] if p["alive"]]


def _by_id(state: dict, pid: str) -> dict | None:
    return next((p for p in state["players"] if p["id"] == pid), None)


def _team_count(state: dict, team: str) -> int:
    return sum(1 for p in _alive(state) if ROLES[p["role"]]["team"] == team)


def _check_win(state: dict) -> str | None:
    if _team_count(state, "mafia") == 0: return "town"
    if _team_count(state, "mafia") >= _team_count(state, "town"): return "mafia"
    return None


def _add_log(state: dict, msg: str) -> None:
    state["log"].append(msg)
    if len(state["log"]) > 60:
        state["log"] = state["log"][-60:]


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_game(session: AsyncSession, chat_id: int) -> MafiaGame | None:
    r = await session.execute(
        select(MafiaGame).where(MafiaGame.chat_id == chat_id, MafiaGame.status != "finished")
    )
    return r.scalar_one_or_none()


async def _save(session: AsyncSession, game: MafiaGame, state: dict) -> None:
    game.set_state(state)
    await session.commit()


async def _host_say(bot: Bot, chat_id: int, text: str, **kwargs) -> int | None:
    """Ведущий говорит в чат. Возвращает message_id."""
    try:
        msg = await bot.send_message(chat_id, f"🎩 <b>Ведущий:</b>\n{text}", parse_mode="HTML", **kwargs)
        return msg.message_id
    except Exception as e:
        log.error("host_say error: %s", e)
        return None


async def _dm(bot: Bot, tg_id: int, text: str, **kwargs) -> None:
    try:
        await bot.send_message(tg_id, text, parse_mode="HTML", **kwargs)
    except Exception:
        pass  # пользователь не запустил личку


# ── Lobby ──────────────────────────────────────────────────────────────────────

@router.message(Command("mafia"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_mafia_start(message: Message, session: AsyncSession, user: User) -> None:
    existing = await _get_game(session, message.chat.id)
    if existing:
        await message.answer("⚠️ В этом чате уже есть игра. Сначала заверши её: /endmafia")
        return

    game = MafiaGame(chat_id=message.chat.id, status="waiting")
    state = _empty_state()
    state["players"].append({
        "id": f"u_{user.telegram_id}", "name": user.first_name,
        "role": "", "alive": True, "is_ai": False,
        "votes": 0, "night_target": None, "suspicions": {}, "protected": False,
        "tg_id": user.telegram_id,
    })
    game.set_state(state)
    session.add(game)
    await session.commit()
    await session.refresh(game)

    from keyboards import mafia_lobby_kb
    await message.answer(
        "🎮 <b>Мафия</b> — лобби открыто!\n\n"
        f"Игроков: <b>1</b>\n"
        f"Нужно минимум: <b>{settings.MAFIA_MIN_PLAYERS}</b> (недостающие — AI)\n\n"
        "Нажми <b>Вступить</b>, когда все собрались — <b>Начать</b>.",
        reply_markup=mafia_lobby_kb(game.id, 1),
    )


@router.callback_query(F.data.startswith("mj:"))
async def cb_join(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    game_id = int(callback.data.split(":")[1])
    r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
    game = r.scalar_one_or_none()
    if not game or game.status != "waiting":
        await callback.answer("Игра не найдена или уже началась.", show_alert=True); return

    state = game.get_state()
    pid = f"u_{user.telegram_id}"
    if any(p["id"] == pid for p in state["players"]):
        await callback.answer("Ты уже в игре!"); return
    if len(state["players"]) >= settings.MAFIA_MAX_PLAYERS:
        await callback.answer("Игра заполнена.", show_alert=True); return

    state["players"].append({
        "id": pid, "name": user.first_name, "role": "", "alive": True,
        "is_ai": False, "votes": 0, "night_target": None, "suspicions": {},
        "protected": False, "tg_id": user.telegram_id,
    })
    await _save(session, game, state)

    from keyboards import mafia_lobby_kb
    count = len(state["players"])
    await callback.message.edit_text(
        f"🎮 <b>Мафия</b> — лобби\n\n"
        f"Игроков: <b>{count}</b>\n"
        + "\n".join(f"• {p['name']}" for p in state["players"]) +
        f"\n\nНужно минимум: <b>{settings.MAFIA_MIN_PLAYERS}</b>",
        reply_markup=mafia_lobby_kb(game.id, count),
    )
    await callback.answer(f"✅ {user.first_name} в игре!")


@router.callback_query(F.data.startswith("ms:"))
async def cb_mafia_begin(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    game_id = int(callback.data.split(":")[1])
    r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
    game = r.scalar_one_or_none()
    if not game or game.status != "waiting":
        await callback.answer("Игра не найдена.", show_alert=True); return

    state = game.get_state()
    # Only first player (creator) or admin can start
    is_creator = state["players"] and state["players"][0]["id"] == f"u_{user.telegram_id}"
    if not is_creator and not user.is_admin:
        await callback.answer("Только создатель может начать игру.", show_alert=True); return

    # Fill with AI
    human_names = [p["name"] for p in state["players"]]
    ai_needed = max(0, settings.MAFIA_MIN_PLAYERS - len(state["players"]))
    available = [n for n in AI_NAMES if n not in human_names]
    random.shuffle(available)
    for i, ai_name in enumerate(available[:ai_needed]):
        state["players"].append({
            "id": f"ai_{i}", "name": ai_name, "role": "", "alive": True,
            "is_ai": True, "votes": 0, "night_target": None, "suspicions": {},
            "protected": False, "tg_id": None,
        })

    # Assign roles
    roles = _role_distribution(len(state["players"]))
    for p, role in zip(state["players"], roles):
        p["role"] = role

    # Notify don about mafia team
    don = next((p for p in state["players"] if p["role"] == "don" and not p["is_ai"]), None)
    if don:
        mafia_names = [p["name"] for p in state["players"]
                       if ROLES[p["role"]]["team"] == "mafia" and p["id"] != don["id"]]
        await _dm(callback.bot, don["tg_id"],
                  f"👑 Ты <b>Дон мафии</b>.\nТвоя команда: {', '.join(mafia_names) or 'только ты'}")

    # DM roles to human players
    player_list = ""
    for p in state["players"]:
        role_info = ROLES[p["role"]]
        player_list += f"{role_info['emoji']} {p['name']}\n"
        if not p["is_ai"]:
            await _dm(callback.bot, p["tg_id"],
                      f"🎭 <b>Твоя роль: {role_info['emoji']} {role_info['name']}</b>\n\n"
                      + _role_desc(p["role"]))

    game.status = "playing"
    state["phase"] = "discuss"
    state["day"] = 1
    await _save(session, game, state)

    intro = await AI.mafia_host(
        f"Игра началась! {len(state['players'])} игроков. "
        f"День 1. Объяви начало первого обсуждения."
    )
    await callback.message.edit_text(
        f"🎮 <b>МАФИЯ НАЧИНАЕТСЯ!</b>\n\n"
        f"<b>Игроки:</b>\n{player_list}\n"
        f"Роли отправлены в личку.\n\n"
        f"🎩 <b>Ведущий:</b>\n{intro}",
    )
    await callback.answer()

    # Launch game loop
    asyncio.create_task(_game_loop(callback.bot, game.id, game.chat_id))


# ── Game loop ──────────────────────────────────────────────────────────────────

async def _game_loop(bot: Bot, game_id: int, chat_id: int) -> None:
    """Основной цикл: день → голосование → ночь → день → ..."""
    try:
        while True:
            async with Session() as session:
                r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
                game = r.scalar_one_or_none()
                if not game or game.status == "finished":
                    return
                state = game.get_state()

            phase = state["phase"]
            if phase == "discuss":
                await _phase_discuss(bot, game_id, chat_id)
            elif phase == "voting":
                await _phase_voting(bot, game_id, chat_id)
            elif phase == "night":
                await _phase_night(bot, game_id, chat_id)
            else:
                return

            # Re-check after phase
            async with Session() as session:
                r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
                game = r.scalar_one_or_none()
                if not game or game.status == "finished":
                    return
    except Exception as e:
        await report_error(e, ctx=f"mafia game_loop game_id={game_id}")
        try:
            await bot.send_message(chat_id, "⚠️ Критическая ошибка в игре. Игра завершена.")
        except Exception:
            pass
        async with Session() as session:
            r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
            game = r.scalar_one_or_none()
            if game:
                game.status = "finished"
                await session.commit()


async def _phase_discuss(bot: Bot, game_id: int, chat_id: int) -> None:
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

    day = state["day"]
    alive = _alive(state)

    # Host announces day
    host_text = await AI.mafia_host(
        f"День {day}. Живые ({len(alive)}): {', '.join(p['name'] for p in alive)}. "
        "Объяви начало дневного обсуждения."
    )
    await _host_say(bot, chat_id, host_text)

    # AI players discuss (staggered over T_DISCUSS)
    ai_players = [p for p in alive if p["is_ai"]]
    discuss_count = min(len(ai_players), 4)  # max 4 AI messages per day
    intervals = sorted(random.sample(range(8, T_DISCUSS - 5), min(discuss_count, T_DISCUSS - 13)))

    elapsed = 0
    for interval in intervals:
        wait = interval - elapsed
        await asyncio.sleep(wait)
        elapsed = interval

        # Pick random AI player that hasn't spoken yet this round
        speaker = random.choice(ai_players)
        recent_log = "\n".join(state["log"][-8:]) or "Тишина, никто ещё ничего не сказал."
        is_mafia = ROLES[speaker["role"]]["team"] == "mafia"
        ctx = (
            f"День {day}. Обсуждение.\n"
            f"Живые: {', '.join(p['name'] for p in _alive(state))}\n"
            f"Последние сообщения:\n{recent_log}\n"
            f"Скажи что-нибудь как {speaker['name']}."
        )
        try:
            msg = await AI.mafia_player(speaker["name"], ROLES[speaker["role"]]["name"], is_mafia, ctx)
            _add_log(state, f"{speaker['name']}: {msg}")
            await bot.send_message(chat_id, f"💬 <b>{speaker['name']}:</b> {msg}")
        except Exception as e:
            log.error("AI discuss error: %s", e)

    remaining = T_DISCUSS - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)

    # Move to voting
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()
        # Reset votes
        for p in state["players"]: p["votes"] = 0
        state["phase"] = "voting"
        await _save(session, game, state)


async def _phase_voting(bot: Bot, game_id: int, chat_id: int) -> None:
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

    alive = _alive(state)
    candidates = [(p["id"], p["name"]) for p in alive]

    host_text = await AI.mafia_host(
        f"День {state['day']}. Объяви начало голосования! "
        f"Кандидаты: {', '.join(p['name'] for p in alive)}"
    )
    from keyboards import mafia_vote_kb, mafia_skip_vote_kb
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    # Combine vote buttons
    b = InlineKeyboardBuilder()
    for pid, name in candidates:
        b.button(text=f"👉 {name}", callback_data=f"mv:{pid}")
    b.button(text="⏭ Пропустить", callback_data="mv:skip")
    b.adjust(2)
    vote_msg = await bot.send_message(
        chat_id,
        f"🎩 <b>Ведущий:</b>\n{host_text}\n\n"
        f"⏱ Голосование — {T_VOTE} секунд!",
        reply_markup=b.as_markup(),
    )

    # AI players vote after short delay
    await asyncio.sleep(6)
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

        alive = _alive(state)
        for ai_p in [p for p in alive if p["is_ai"]]:
            target = _ai_vote_target(ai_p, state)
            if target:
                target["votes"] += 1
                _update_suspicions(ai_p, state)
        await _save(session, game, state)

    await asyncio.sleep(T_VOTE - 6)

    # Remove vote keyboard
    try:
        await bot.edit_message_reply_markup(chat_id, vote_msg.message_id, reply_markup=None)
    except Exception:
        pass

    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

        alive = _alive(state)
        if alive:
            max_votes = max(p["votes"] for p in alive)
            top = [p for p in alive if p["votes"] == max_votes and max_votes > 0]

            if len(top) == 1:
                victim = top[0]
                victim["alive"] = False
                role_info = ROLES[victim["role"]]
                elim_text = await AI.mafia_host(
                    f"Игрок {victim['name']} выгнан голосованием. Роль: {role_info['name']}. "
                    "Объяви это с драмой."
                )
                await bot.send_message(
                    chat_id,
                    f"🎩 <b>Ведущий:</b>\n{elim_text}\n\n"
                    f"☠️ <b>{victim['name']}</b> покидает город.\n"
                    f"Роль: {role_info['emoji']} {role_info['name']}"
                )
                _add_log(state, f"[ДЕНЬ {state['day']}] {victim['name']} выгнан. Роль: {role_info['name']}")
            else:
                skip_text = await AI.mafia_host("Голоса разделились, никто не выгнан. Прокомментируй.")
                await bot.send_message(chat_id, f"🎩 <b>Ведущий:</b>\n{skip_text}")

        winner = _check_win(state)
        if winner:
            await _end_game(bot, chat_id, game, state, session, winner)
            return

        state["phase"] = "night"
        await _save(session, game, state)


async def _phase_night(bot: Bot, game_id: int, chat_id: int) -> None:
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

        # Reset night targets
        for p in state["players"]:
            p["night_target"] = None
            p["protected"] = False
        state["protected_id"] = None
        await _save(session, game, state)

    alive = _alive(state)
    host_text = await AI.mafia_host(
        f"Ночь {state['day']}. Живые: {', '.join(p['name'] for p in alive)}. "
        "Объяви наступление ночи."
    )
    await bot.send_message(chat_id, f"🎩 <b>Ведущий:</b>\n{host_text}")

    # Send night action prompts to human players
    from keyboards import mafia_night_kb
    for p in alive:
        if p["is_ai"] or not p["tg_id"]:
            continue
        role_info = ROLES[p["role"]]
        if not role_info["night"]:
            continue
        action = role_info["action"]
        if action == "kill":
            targets = [(t["id"], t["name"]) for t in alive if ROLES[t["role"]]["team"] != "mafia"]
            label = "🔫 Выбери жертву для убийства:"
        elif action == "check":
            targets = [(t["id"], t["name"]) for t in alive if t["id"] != p["id"]]
            label = "🔍 Выбери игрока для проверки:"
        elif action == "heal":
            targets = [(t["id"], t["name"]) for t in alive]
            label = "💊 Выбери игрока для защиты:"
        else:
            continue
        await _dm(bot, p["tg_id"], label, reply_markup=mafia_night_kb(targets, action))

    # AI night actions immediately
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

        alive = _alive(state)
        for ai_p in [p for p in alive if p["is_ai"]]:
            role_info = ROLES[ai_p["role"]]
            if not role_info["night"]: continue
            target = _ai_night_target(ai_p, state)
            if not target: continue
            action = role_info["action"]
            if action == "kill":
                ai_p["night_target"] = target["id"]
            elif action == "heal":
                state["protected_id"] = target["id"]
        await _save(session, game, state)

    await asyncio.sleep(T_NIGHT)

    # Resolve night
    async with Session() as session:
        r = await session.execute(select(MafiaGame).where(MafiaGame.id == game_id))
        game = r.scalar_one_or_none()
        if not game: return
        state = game.get_state()

        # Tally mafia votes
        kill_votes: dict[str, int] = {}
        for p in state["players"]:
            if p["alive"] and ROLES[p["role"]]["team"] == "mafia" and p["night_target"]:
                kill_votes[p["night_target"]] = kill_votes.get(p["night_target"], 0) + 1

        killed_name = None
        was_saved = False
        if kill_votes:
            victim_id = max(kill_votes, key=lambda k: kill_votes[k])
            if victim_id == state["protected_id"]:
                was_saved = True
            else:
                victim = _by_id(state, victim_id)
                if victim:
                    victim["alive"] = False
                    killed_name = victim["name"]
                    _add_log(state, f"[НОЧЬ {state['day']}] {killed_name} убит мафией.")

        # Detective results (send DM to human detective)
        for p in state["players"]:
            if p["alive"] and p["role"] == "detective" and not p["is_ai"] and p["night_target"] and p["tg_id"]:
                target = _by_id(state, p["night_target"])
                if target:
                    side = "🔴 МАФИЯ" if ROLES[target["role"]]["team"] == "mafia" else "🔵 Мирный"
                    await _dm(bot, p["tg_id"],
                              f"🔍 <b>Результат проверки:</b>\n"
                              f"{target['name']} — {side}")

        night_text = await AI.mafia_host(
            ("Доктор спас жертву этой ночью! Объяви это." if was_saved else
             f"Ночью был убит {killed_name}. Объяви трагически." if killed_name else
             "Тихая ночь — никто не погиб. Прокомментируй.")
        )
        await bot.send_message(
            chat_id,
            f"🎩 <b>Ведущий:</b>\n{night_text}\n\n"
            + (f"💊 Доктор спас чью-то жизнь!" if was_saved else
               f"💀 <b>{killed_name}</b> не пережил ночь." if killed_name else
               "🌅 Рассвет. Все живы.")
        )

        winner = _check_win(state)
        if winner:
            await _end_game(bot, chat_id, game, state, session, winner)
            return

        state["phase"] = "discuss"
        state["day"] += 1
        await _save(session, game, state)


async def _end_game(bot: Bot, chat_id: int, game: MafiaGame, state: dict,
                    session: AsyncSession, winner: str) -> None:
    state["phase"] = "finished"
    state["winner"] = winner
    game.status = "finished"
    game.finished_at = datetime.utcnow()
    await _save(session, game, state)

    end_text = await AI.mafia_host(
        f"Игра окончена! Победила {'мафия' if winner == 'mafia' else 'команда города'}. "
        "Объяви победителя торжественно."
    )
    roles_reveal = "\n".join(
        f"{ROLES[p['role']]['emoji']} {p['name']} — {ROLES[p['role']]['name']} "
        f"{'💀' if not p['alive'] else '✅'}"
        for p in state["players"]
    )
    w_text = "🔫 <b>Мафия победила!</b>" if winner == "mafia" else "🏙 <b>Город победил!</b>"
    await bot.send_message(
        chat_id,
        f"🎩 <b>Ведущий:</b>\n{end_text}\n\n"
        f"{w_text}\n\n"
        f"<b>Роли игроков:</b>\n{roles_reveal}",
    )


# ── Vote / Night callbacks ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mv:"))
async def cb_vote(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    target_id = callback.data.split(":")[1]
    game = await _get_game(session, callback.message.chat.id)
    if not game:
        await callback.answer("Нет активной игры."); return

    state = game.get_state()
    if state["phase"] != "voting":
        await callback.answer("Сейчас не фаза голосования."); return

    voter = _by_id(state, f"u_{user.telegram_id}")
    if not voter or not voter["alive"]:
        await callback.answer("Ты не можешь голосовать."); return

    if target_id != "skip":
        target = _by_id(state, target_id)
        if not target or not target["alive"]:
            await callback.answer("Цель недействительна."); return
        target["votes"] += 1
        await callback.answer(f"🗳 Ты проголосовал за {target['name']}!")
    else:
        await callback.answer("⏭ Ты пропустил голосование.")

    await _save(session, game, state)


@router.callback_query(F.data.startswith("mn_"))
async def cb_night_action(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    # mn_kill:pid | mn_check:pid | mn_heal:pid
    parts = callback.data.split(":")
    action = parts[0].replace("mn_", "")
    target_id = parts[1]

    # Find game by user participation (DM context — no chat_id)
    from sqlalchemy import text
    r = await session.execute(
        select(MafiaGame).where(MafiaGame.status == "playing")
    )
    games = r.scalars().all()
    game = None
    for g in games:
        s = g.get_state()
        if any(p["id"] == f"u_{user.telegram_id}" for p in s["players"]):
            game = g; state = s; break

    if not game:
        await callback.answer("Игра не найдена.", show_alert=True); return
    if state["phase"] != "night":
        await callback.answer("Сейчас не ночь."); return

    player = _by_id(state, f"u_{user.telegram_id}")
    if not player or not player["alive"]:
        await callback.answer("Ты не можешь действовать."); return

    target = _by_id(state, target_id)
    if not target:
        await callback.answer("Цель не найдена."); return

    if action == "kill":
        player["night_target"] = target_id
        await callback.answer(f"🔫 Цель выбрана: {target['name']}")
    elif action == "check":
        player["night_target"] = target_id
        # DM result will be sent at night resolution
        await callback.answer(f"🔍 Проверяешь: {target['name']}")
    elif action == "heal":
        state["protected_id"] = target_id
        await callback.answer(f"💊 Защищаешь: {target['name']}")

    await _save(session, game, state)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── /endmafia ─────────────────────────────────────────────────────────────────

@router.message(Command("endmafia"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_endmafia(message: Message, session: AsyncSession, user: User) -> None:
    if not user.is_admin:
        await message.answer("⛔ Только администратор может завершить игру."); return
    game = await _get_game(session, message.chat.id)
    if not game:
        await message.answer("Нет активной игры."); return
    game.status = "finished"
    game.finished_at = datetime.utcnow()
    await session.commit()
    await message.answer("🏁 Игра завершена администратором.")


# ── AI helpers ─────────────────────────────────────────────────────────────────

def _ai_vote_target(player: dict, state: dict) -> dict | None:
    alive = [p for p in _alive(state) if p["id"] != player["id"]]
    if not alive: return None
    is_mafia = ROLES[player["role"]]["team"] == "mafia"
    if is_mafia:
        town = [p for p in alive if ROLES[p["role"]]["team"] == "town"]
        return random.choice(town) if town else random.choice(alive)
    # Town: vote for most suspected
    suspects = player.get("suspicions", {})
    if suspects:
        by_sus = sorted(suspects.items(), key=lambda x: x[1], reverse=True)
        for name, _ in by_sus:
            t = next((p for p in alive if p["name"] == name), None)
            if t: return t
    return random.choice(alive)


def _ai_night_target(player: dict, state: dict) -> dict | None:
    alive = _alive(state)
    action = ROLES[player["role"]].get("action")
    if action == "kill":
        town = [p for p in alive if ROLES[p["role"]]["team"] == "town"]
        return random.choice(town) if town else None
    elif action == "heal":
        return random.choice(alive) if alive else None
    elif action == "check":
        others = [p for p in alive if p["id"] != player["id"]]
        return random.choice(others) if others else None
    return None


def _update_suspicions(player: dict, state: dict) -> None:
    alive = [p for p in _alive(state) if p["id"] != player["id"]]
    if not alive: return
    t = random.choice(alive)
    sups = player.setdefault("suspicions", {})
    sups[t["name"]] = sups.get(t["name"], 0) + random.randint(1, 3)


def _role_desc(role: str) -> str:
    descs = {
        "civilian":  "Обычный житель. Голосуй днём, ищи мафию.",
        "detective": "Каждую ночь проверяй одного игрока — узнаешь его принадлежность.",
        "doctor":    "Каждую ночь защищай одного игрока от убийства.",
        "mafia":     "Ночью убивай мирных жителей. Прячься.",
        "don":       "Глава мафии. Видишь команду. Убиваешь ночью.",
    }
    return descs.get(role, "")
