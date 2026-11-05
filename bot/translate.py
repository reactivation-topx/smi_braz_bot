"""Бесплатный машинный перевод португальский → русский.

Два варианта (TRANSLATE_PROVIDER):
  mymemory       — онлайн-сервис MyMemory, ничего ставить не нужно (по умолчанию).
                   Лимит: 5 000 символов в сутки без почты, 50 000 — если указать TRANSLATE_EMAIL.
                   Бот переводит только то, что реально отправляет, — это несколько тысяч символов в день.
  libretranslate — свой сервер LibreTranslate (TRANSLATE_URL), без лимитов, но нужно 2–4 GB памяти.
  off            — без перевода.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

log = logging.getLogger(__name__)

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_MAX_BYTES = 480  # у MyMemory лимит 500 байт на запрос


class Translator:
    def __init__(self, provider: str = "mymemory", url: str = "", email: str = "",
                 source: str = "pt", target: str = "ru"):
        self.provider = (provider or "off").lower()
        self.url = (url or "").rstrip("/")
        self.email = email
        self.source = source
        self.target = target
        self._blocked_until: dt.date | None = None
        if self.provider == "libretranslate" and not self.url:
            self.provider = "off"

    @property
    def enabled(self) -> bool:
        return self.provider in ("mymemory", "libretranslate")

    @property
    def label(self) -> str:
        if self.provider == "mymemory":
            return "MyMemory (онлайн, бесплатно)" + ("" if self.email else " — без почты, лимит 5 000 символов/сутки")
        if self.provider == "libretranslate":
            return "LibreTranslate (свой сервер)"
        return "выключен"

    async def translate(self, texts: list[str]) -> list[str | None]:
        """Переводит пачку строк. При любой ошибке — None (покажем оригинал)."""
        if not self.enabled or not texts:
            return [None] * len(texts)
        if self._blocked_until and dt.date.today() <= self._blocked_until:
            return [None] * len(texts)
        try:
            if self.provider == "libretranslate":
                return await self._libre(texts)
            return await self._mymemory(texts)
        except Exception as e:  # noqa: BLE001
            log.warning("Переводчик недоступен: %s", e)
            return [None] * len(texts)

    # ───────────── MyMemory ─────────────
    async def _mymemory(self, texts: list[str]) -> list[str | None]:
        import aiohttp

        sem = asyncio.Semaphore(3)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:

            async def one(text: str) -> str | None:
                text = _clip_bytes((text or "").strip(), MYMEMORY_MAX_BYTES)
                if not text or self._blocked_until:
                    return None
                params = {"q": text, "langpair": f"{self.source}|{self.target}"}
                if self.email:
                    params["de"] = self.email
                async with sem:
                    async with session.get(MYMEMORY_URL, params=params) as r:
                        data = await r.json(content_type=None)
                status = int(data.get("responseStatus") or r.status) if isinstance(data, dict) else r.status
                out = ((data.get("responseData") or {}).get("translatedText") or "") if isinstance(data, dict) else ""
                if status in (403, 429) or "MYMEMORY WARNING" in out.upper():
                    # дневной лимит исчерпан — не долбим сервис до завтра
                    self._blocked_until = dt.date.today()
                    log.warning("MyMemory: дневной лимит исчерпан, до завтра новости будут без перевода")
                    return None
                if status != 200 or not out.strip():
                    return None
                return out.strip()

            return list(await asyncio.gather(*(one(t) for t in texts)))

    # ───────────── LibreTranslate ─────────────
    async def _libre(self, texts: list[str]) -> list[str | None]:
        import aiohttp

        payload = {"q": texts, "source": self.source, "target": self.target, "format": "text"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.post(f"{self.url}/translate", json=payload) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(data.get("error") if isinstance(data, dict) else r.status)
        res = data.get("translatedText") if isinstance(data, dict) else None
        if isinstance(res, str):
            res = [res]
        if not isinstance(res, list) or len(res) != len(texts):
            return [None] * len(texts)
        return [(t or "").strip() or None for t in res]


def _clip_bytes(text: str, limit: int) -> str:
    raw = text.encode()
    if len(raw) <= limit:
        return text
    cut = raw[: limit - 3].decode(errors="ignore")
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
