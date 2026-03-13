"""
AI-провайдеры и менеджер запросов.
Добавить нового провайдера: унаследуй AIProvider, добавь в PROVIDERS.
"""
import logging
from abc import ABC, abstractmethod

import aiohttp
from groq import AsyncGroq

from config import settings

log = logging.getLogger(__name__)


# ── Base ───────────────────────────────────────────────────────────────────────

class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def ask(self, messages: list[dict], system: str, temperature: float = 0.8, max_tokens: int = 900) -> str: ...

    @abstractmethod
    def available(self) -> bool: ...


# ── Groq ───────────────────────────────────────────────────────────────────────

class GroqProvider(AIProvider):
    name = "groq"
    _client: AsyncGroq | None = None

    def available(self) -> bool:
        return bool(settings.GROQ_API_KEY)

    def _cli(self) -> AsyncGroq:
        if not self._client:
            self._client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        return self._client

    async def ask(self, messages: list[dict], system: str, temperature: float = 0.8, max_tokens: int = 900) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        try:
            r = await self._cli().chat.completions.create(
                model=settings.GROQ_MODEL, messages=msgs,
                temperature=temperature, max_tokens=max_tokens,
            )
            return r.choices[0].message.content or ""
        except Exception as e:
            log.error("Groq error: %s", e)
            raise


# ── Cerberus ───────────────────────────────────────────────────────────────────

class CerberProvider(AIProvider):
    name = "cerber"

    def available(self) -> bool:
        return bool(settings.CERBER_API_KEY)

    async def ask(self, messages: list[dict], system: str, temperature: float = 0.8, max_tokens: int = 900) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {"model": settings.CERBER_MODEL, "messages": msgs,
                   "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {settings.CERBER_API_KEY}", "Content-Type": "application/json"}
        url = f"{settings.CERBER_API_URL.rstrip('/')}/chat/completions"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Cerber HTTP {resp.status}: {await resp.text()}")
                    d = await resp.json()
                    return d["choices"][0]["message"]["content"] or ""
        except Exception as e:
            log.error("Cerber error: %s", e)
            raise


PROVIDERS: dict[str, AIProvider] = {
    "groq": GroqProvider(),
    "cerber": CerberProvider(),
}


def get_provider(name: str | None = None) -> AIProvider:
    p = PROVIDERS.get(name or settings.DEFAULT_AI_PROVIDER)
    if p is None or not p.available():
        # fallback to first available
        for pv in PROVIDERS.values():
            if pv.available():
                return pv
        raise RuntimeError("Нет доступных AI провайдеров. Проверь API ключи.")
    return p


# ── System prompts ─────────────────────────────────────────────────────────────

PERSONA = """Ты — AI-ассистент «Бот». Характер:
• Саркастичный, иногда грубоватый
• Отвечает неохотно, как будто оторвали от дел
• Умный и компетентный, но показывает это с ленцой
• Иногда ворчит, иногда шутит — юмор чёрный
• Несмотря на характер — реально помогает
Отвечай на русском, кратко и по делу."""

MAFIA_HOST = """Ты — ведущий игры «Мафия». Ты:
• Объявляешь фазы с драмой и атмосферой
• Комментируешь события напряжённо
• Беспристрастен, но создаёшь интригу
• Пишешь живо, 2-4 предложения
Отвечай на русском."""

STORY_SYSTEM = """Ты — нарратор интерактивной истории жанр: {genre}.
Правила:
• Пиши атмосферно, 3-5 предложений на фрагмент
• Каждый фрагмент заканчивается РОВНО тремя вариантами:
  1) действие
  2) действие
  3) действие
• Выборы игрока реально меняют сюжет
• Отвечай на русском."""


# ── Public API ─────────────────────────────────────────────────────────────────

async def chat(user_msg: str, history: list[dict], provider: str | None = None) -> str:
    msgs = history + [{"role": "user", "content": user_msg}]
    return await get_provider(provider).ask(msgs, system=PERSONA, temperature=0.85)


async def mafia_host(prompt: str) -> str:
    return await get_provider().ask([{"role": "user", "content": prompt}], system=MAFIA_HOST, temperature=0.75, max_tokens=300)


async def mafia_player(name: str, role: str, is_mafia: bool, context: str, provider: str | None = None) -> str:
    goal = "помочь мафии победить, не раскрывая себя" if is_mafia else "вычислить и выгнать мафию"
    side = "Ты мафиози — скрывай это, сей подозрения на других." if is_mafia else "Ты мирный — ищи мафию логикой."
    system = (
        f"Ты игрок в Мафию. Имя: {name}. Роль: {role}.\n"
        f"{side}\nЦель: {goal}.\n"
        "Пиши как живой игрок в чате: 1-2 предложения, без объяснений роли."
    )
    return await get_provider(provider).ask([{"role": "user", "content": context}], system=system, temperature=0.92, max_tokens=150)


async def story_gen(genre: str, history: list[dict], choice: str | None = None, provider: str | None = None) -> str:
    system = STORY_SYSTEM.format(genre=genre)
    msgs = []
    for h in history:
        msgs.append({"role": "assistant", "content": h.get("text", "")})
        if h.get("choice"):
            msgs.append({"role": "user", "content": f"Выбор: {h['choice']}"})
    if choice:
        msgs.append({"role": "user", "content": f"Выбор: {choice}"})
    elif not history:
        msgs.append({"role": "user", "content": "Начни историю. Установи сцену и дай первые варианты."})
    return await get_provider(provider).ask(msgs, system=system, temperature=0.9, max_tokens=600)
