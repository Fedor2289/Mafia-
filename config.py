"""Конфигурация, логирование и уведомление об ошибках."""
import logging
import os
import traceback
from datetime import datetime
from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.FileHandler("logs/errors.log", encoding="utf-8"),
    ],
)
logging.getLogger("logs/errors.log").setLevel(logging.ERROR)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str
    DATABASE_URL: str

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    CERBER_API_KEY: str = ""
    CERBER_API_URL: str = "https://api.cerberai.com/v1"
    CERBER_MODEL: str = "cerberus-xl"

    DEFAULT_AI_PROVIDER: str = "groq"
    ADMIN_IDS: str = ""

    MAX_HISTORY: int = 20
    MAFIA_MIN_PLAYERS: int = 4
    MAFIA_MAX_PLAYERS: int = 10
    # Если True — бот отвечает на ВСЕ сообщения в группе, не только на упоминания
    GROUP_AI_ALL: bool = False

    @cached_property
    def admin_list(self) -> list[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]


settings = Settings()

# ── Error reporter ─────────────────────────────────────────────────────────────
_bot = None


def set_error_bot(bot) -> None:
    global _bot
    _bot = bot


async def report_error(exc: Exception, ctx: str = "", user_id: int | None = None) -> None:
    tb = traceback.format_exc()
    logging.getLogger("errors").error("Error in %s: %s\n%s", ctx, exc, tb)
    if not _bot or not settings.admin_list:
        return
    short = tb[-1800:] if len(tb) > 1800 else tb
    ts = datetime.now().strftime("%d.%m %H:%M:%S")
    msg = (
        f"🚨 <b>Ошибка</b> | <code>{ts}</code>\n"
        f"📍 <code>{ctx[:120]}</code>\n"
        f"👤 user_id: <code>{user_id or '—'}</code>\n"
        f"❌ <b>{type(exc).__name__}:</b> <code>{str(exc)[:200]}</code>\n\n"
        f"<pre>{short}</pre>"
    )
    for aid in settings.admin_list:
        try:
            await _bot.send_message(aid, msg, parse_mode="HTML")
        except Exception:
            pass
