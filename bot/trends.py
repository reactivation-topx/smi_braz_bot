"""Тренды поиска Google по Бразилии (бесплатная RSS-лента «Trending now»)."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

DEFAULT_URL = "https://trends.google.com/trending/rss?geo=BR"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_traffic(raw: str) -> int:
    """'200.000+', '50 mil+', '2M+', '10K+' → число."""
    s = (raw or "").strip().lower().replace(" ", "")
    mult = 1
    if s.endswith("+"):
        s = s[:-1]
    for suffix, m in (("mil", 1_000), ("k", 1_000), ("mi", 1_000_000), ("m", 1_000_000)):
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], m
            break
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) * mult if digits else 0


def parse_trends(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        term, traffic, news_titles = "", 0, []
        for ch in item.iter():
            name = _local(ch.tag)
            if name == "title" and not term:
                term = (ch.text or "").strip()
            elif name == "approx_traffic":
                traffic = parse_traffic(ch.text or "")
            elif name == "news_item_title":
                news_titles.append((ch.text or "").strip())
        if term:
            out.append({"term": term, "traffic": traffic, "news": news_titles})
    return out


async def fetch_trends(url: str = DEFAULT_URL) -> list[dict]:
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SMIBot/1.0)"}) as r:
                r.raise_for_status()
                return parse_trends(await r.read())
    except Exception as e:  # noqa: BLE001
        log.warning("Тренды Google недоступны: %s", e)
        return []
