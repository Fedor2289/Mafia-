"""Точка входа."""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import settings, set_error_bot, report_error
from database import create_tables, Session, get_user
import handlers, mafia, story

log = logging.getLogger(__name__)


# ── Middlewares ────────────────────────────────────────────────────────────────

class DBMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        async with Session() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        session = data.get("session")
        tg_user = None
        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_user = event.from_user
        if tg_user and session:
            data["user"] = await get_user(session, tg_user.id, tg_user.username, tg_user.first_name or "User")
        return await handler(event, data)


class ErrorMiddleware(BaseMiddleware):
    """Ловит все необработанные исключения, логирует и уведомляет админов."""
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            user = data.get("user")
            uid = user.telegram_id if user else None
            ctx = ""
            if isinstance(event, Message):
                ctx = f"Message | text='{(event.text or '')[:80]}'"
                try: await event.answer("⚠️ Ошибка. Администраторы уведомлены.")
                except Exception: pass
            elif isinstance(event, CallbackQuery):
                ctx = f"Callback | data='{event.data}'"
                try: await event.answer("⚠️ Ошибка.", show_alert=False)
                except Exception: pass
            await report_error(exc, ctx=ctx, user_id=uid)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    log.info("Starting bot…")
    await create_tables()
    log.info("DB ready.")

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    set_error_bot(bot)

    dp = Dispatcher(storage=MemoryStorage())

    for mw in (ErrorMiddleware(), DBMiddleware(), UserMiddleware()):
        dp.message.middleware(mw)
        dp.callback_query.middleware(mw)

    dp.include_router(handlers.router)
    dp.include_router(mafia.router)
    dp.include_router(story.router)

    log.info("Polling…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        log.info("Stopped.")


if __name__ == "__main__":
    asyncio.run(main())
