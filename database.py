"""Все модели SQLAlchemy и работа с сессией."""
import json
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings


def _fix_db_url(url: str) -> str:
    """
    Railway даёт DATABASE_URL как 'postgresql://...' или 'postgres://...'
    SQLAlchemy + asyncpg требует 'postgresql+asyncpg://...'
    Эта функция автоматически исправляет формат.
    """
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


_db_url = _fix_db_url(settings.DATABASE_URL)
engine = create_async_engine(
    _db_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="User")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32), default="groq")
    history_json: Mapped[str] = mapped_column(Text, default="[]")
    group_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def get_history(self) -> list[dict]:
        return json.loads(self.history_json or "[]")

    def add_message(self, role: str, content: str) -> None:
        h = self.get_history()
        h.append({"role": role, "content": content})
        if len(h) > settings.MAX_HISTORY * 2:
            h = h[-(settings.MAX_HISTORY * 2):]
        self.history_json = json.dumps(h, ensure_ascii=False)

    def clear_history(self) -> None:
        self.history_json = "[]"


class MafiaGame(Base):
    __tablename__ = "mafia_games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def get_state(self) -> dict:
        return json.loads(self.state_json or "{}")

    def set_state(self, s: dict) -> None:
        self.state_json = json.dumps(s, ensure_ascii=False, default=str)


class Story(Base):
    __tablename__ = "stories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    genre: Mapped[str] = mapped_column(String(32), default="horror")
    history_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def get_history(self) -> list[dict]:
        return json.loads(self.history_json or "[]")

    def set_history(self, h: list) -> None:
        self.history_json = json.dumps(h, ensure_ascii=False)


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str,
) -> User:
    from sqlalchemy import select
    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    u = r.scalar_one_or_none()
    if u is None:
        u = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            is_admin=telegram_id in settings.admin_list,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
    else:
        changed = False
        if u.username != username:
            u.username = username
            changed = True
        if u.first_name != first_name:
            u.first_name = first_name
            changed = True
        if changed:
            await session.commit()
    return u
