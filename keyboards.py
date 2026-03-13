"""Все клавиатуры бота."""
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ── Reply ──────────────────────────────────────────────────────────────────────

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🤖 AI чат"),      KeyboardButton(text="🎮 Мафия")],
        [KeyboardButton(text="📖 История"),     KeyboardButton(text="⚙️ Настройки")],
    ], resize_keyboard=True)


# ── Settings ───────────────────────────────────────────────────────────────────

def settings_kb(provider: str, group_ai: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n, lbl in [("groq", "🤖 Groq (Llama)"), ("cerber", "🐺 Cerberus AI")]:
        mark = "✅ " if provider == n else ""
        b.button(text=f"{mark}{lbl}", callback_data=f"set_prov:{n}")
    b.button(
        text=f"{'✅' if group_ai else '☐'} Отвечать в группах",
        callback_data="toggle_group_ai",
    )
    b.button(text="🗑 Очистить историю", callback_data="clear_history")
    b.adjust(2, 1, 1)
    return b.as_markup()


# ── Mafia ──────────────────────────────────────────────────────────────────────

def mafia_lobby_kb(game_id: int, count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"✋ Вступить ({count} игр.)", callback_data=f"mj:{game_id}")
    b.button(text="▶️ Начать игру",              callback_data=f"ms:{game_id}")
    b.adjust(1)
    return b.as_markup()


def mafia_vote_kb(players: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """players: [(player_id, name), ...]"""
    b = InlineKeyboardBuilder()
    for pid, name in players:
        b.button(text=f"👉 {name}", callback_data=f"mv:{pid}")
    b.adjust(2)
    return b.as_markup()


def mafia_night_kb(targets: list[tuple[str, str]], action: str) -> InlineKeyboardMarkup:
    """action: kill | check | heal"""
    b = InlineKeyboardBuilder()
    for pid, name in targets:
        b.button(text=name, callback_data=f"mn_{action}:{pid}")
    b.adjust(2)
    return b.as_markup()


def mafia_skip_vote_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏭ Пропустить (не выгонять)", callback_data="mv:skip")
    return b.as_markup()


# ── Story ──────────────────────────────────────────────────────────────────────

GENRES = {
    "horror":    "🩸 Хоррор",
    "detective": "🔎 Детектив",
    "scifi":     "🚀 Фантастика",
    "fantasy":   "⚔️ Фэнтези",
}


def story_genre_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in GENRES.items():
        b.button(text=label, callback_data=f"sg:{key}")
    b.adjust(2)
    return b.as_markup()


def story_choices_kb(choices: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, c in enumerate(choices, 1):
        lbl = c[:35] + "…" if len(c) > 35 else c
        b.button(text=f"{i}) {lbl}", callback_data=f"sc:{i}")
    b.button(text="❌ Завершить историю", callback_data="send")
    b.adjust(1)
    return b.as_markup()


# ── Admin ──────────────────────────────────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статистика",           callback_data="adm:stats")
    b.button(text="🐛 Ошибки (лог)",         callback_data="adm:errlog")
    b.button(text="📋 Общий лог",            callback_data="adm:alllog")
    b.button(text="📢 Написать за юзера",    callback_data="adm:imp")
    b.button(text="🎭 AI персонаж",          callback_data="adm:ai_char")
    b.button(text="📣 Рассылка",             callback_data="adm:broadcast")
    b.button(text="🔄 Сбросить игры здесь",  callback_data="adm:reset")
    b.button(text="👑 Управление админами",  callback_data="adm:admins")
    b.adjust(2)
    return b.as_markup()


def back_kb(cb: str = "adm:back") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data=cb)
    b.button(text="❌ Закрыть", callback_data="adm:close")
    b.adjust(2)
    return b.as_markup()


def confirm_kb(yes: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=yes)
    b.button(text="❌ Отмена",      callback_data="adm:back")
    b.adjust(2)
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="adm:cancel")
    return b.as_markup()
