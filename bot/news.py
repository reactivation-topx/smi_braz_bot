"""Сбор новостей из RSS-лент (без внешних парсеров — только стандартная библиотека)."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; SMIBot/1.0; RSS reader)"
MAX_FEED_BYTES = 5_000_000
MAX_IMAGE_BYTES = 4_000_000
MAX_AGE_HOURS = 12  # старее — не считаем новостью

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
_WS_RE = re.compile(r"\s+")


def load_sources(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [s for s in data if s.get("url")]


def strip_html(text: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    return _WS_RE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return _WS_RE.sub(" ", t).strip()


def news_id(title: str) -> str:
    """Один и тот же заголовок из разных лент = одна новость."""
    return hashlib.sha1(normalize_title(title).encode()).hexdigest()[:20]


def _parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def _text(el, path: str) -> str:
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_feed(xml_bytes: bytes, source: dict) -> list[dict]:
    """RSS 2.0 / RSS 0.91 / Atom → список новостей."""
    root = ET.fromstring(xml_bytes)
    items = root.findall(".//item") or root.findall(".//atom:entry", NS)
    out = []
    for it in items:
        title = strip_html(_text(it, "title") or _text(it, "atom:title"))
        if not title:
            continue
        link = _text(it, "link")
        if not link:
            link_el = it.find("atom:link", NS)
            link = link_el.get("href", "") if link_el is not None else ""
        raw_desc = _text(it, "description") or _text(it, "atom:summary") or _text(it, "content:encoded")
        published = _parse_date(
            _text(it, "pubDate") or _text(it, "atom:published") or _text(it, "atom:updated") or _text(it, "dc:date")
        )
        source_name = source["name"]
        # Google Новости: «Заголовок - Издание» + тег <source>
        src_el = it.find("source")
        if src_el is not None and (src_el.text or "").strip():
            outlet = src_el.text.strip()
            if title.endswith(f" - {outlet}"):
                title = title[: -len(outlet) - 3].strip()
            source_name = outlet
        image = None
        for path in ("media:content", "media:thumbnail"):
            m = it.find(path, NS)
            if m is not None and m.get("url"):
                image = m.get("url")
                break
        if not image:
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url"):
                image = enc.get("url")
        if not image and raw_desc:
            m = _IMG_RE.search(raw_desc)
            if m:
                image = html.unescape(m.group(1))
        summary = strip_html(raw_desc)
        if "news.google." in source.get("url", ""):
            summary = ""  # у Google Новостей описание — это просто список ссылок
        out.append(
            {
                "id": news_id(title),
                "source": source_name,
                "topic": source.get("topic", "general"),
                "url": link,
                "title": title,
                "summary": summary[:600],
                "image_url": image,
                "published_at": (published or dt.datetime.now(dt.timezone.utc)).isoformat(),
            }
        )
    return out


async def fetch_feed(session, source: dict) -> list[dict]:
    async with session.get(source["url"], headers={"User-Agent": USER_AGENT}) as resp:
        resp.raise_for_status()
        data = await resp.content.read(MAX_FEED_BYTES)
    return parse_feed(data, source)


async def collect(db, sources: list[dict]) -> int:
    """Опросить все ленты, сохранить новые свежие новости. Возвращает их число."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=25)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)
    added = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(*(fetch_feed(session, s) for s in sources), return_exceptions=True)
    for src, res in zip(sources, results):
        if isinstance(res, Exception):
            log.warning("Лента %s недоступна: %s", src["name"], res)
            db.feed_error(src["name"], src["url"], f"{type(res).__name__}: {res}")
            continue
        db.feed_ok(src["name"], src["url"], len(res))
        for item in res:
            if dt.datetime.fromisoformat(item["published_at"]) < cutoff:
                continue
            if db.add_news(item):
                added += 1
    return added


async def download_image(url: str | None) -> bytes | None:
    """Картинка к новости для карточки. Любая ошибка — просто без фото."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status != 200 or not (resp.headers.get("Content-Type", "").startswith("image")):
                    return None
                data = await resp.content.read(MAX_IMAGE_BYTES + 1)
                return data if len(data) <= MAX_IMAGE_BYTES else None
    except Exception as e:  # noqa: BLE001
        log.debug("Не удалось скачать картинку %s: %s", url, e)
        return None
