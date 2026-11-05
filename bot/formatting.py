"""Единый текстовый формат сообщений: даты по-русски, подписи к карточкам."""
from __future__ import annotations

import datetime as dt
import html

from .ai import NEWS_CATEGORIES
from .calendar_engine import CATEGORIES, Occurrence
from .states import scope_label, state_name

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def esc_attr(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def countdown(occ: Occurrence, today: dt.date) -> str:
    d = occ.days_until(today)
    if d < 0 and occ.end >= today:
        return "идёт сейчас"
    if d == 0:
        return "сегодня"
    if d == 1:
        return "завтра"
    if d == 2:
        return "послезавтра"
    return f"через {days_word(d)}"


def date_long(d: dt.date, with_year: bool = False, with_weekday: bool = True) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if with_year:
        s += f" {d.year}"
    if with_weekday:
        s = f"{WEEKDAYS_SHORT[d.weekday()]}, {s}"
    return s


def date_range(start: dt.date, end: dt.date, today: dt.date | None = None) -> str:
    with_year = today is not None and (start.year != today.year or end.year != today.year)
    if start == end:
        return date_long(start, with_year)
    if start.month == end.month and start.year == end.year:
        tail = f" {start.year}" if with_year else ""
        return f"{start.day}–{end.day} {MONTHS_GEN[start.month - 1]}{tail} ({WEEKDAYS_SHORT[start.weekday()]}–{WEEKDAYS_SHORT[end.weekday()]})"
    return f"{date_long(start, with_year)} – {date_long(end, with_year)}"


def remind_schedule(days: list[int]) -> str:
    """[5, 3, 1, 0] → «за 5, 3 и 1 день и в день события»."""
    ahead = sorted((d for d in days if d > 0), reverse=True)
    parts = []
    if ahead:
        nums = ", ".join(map(str, ahead[:-1])) + (" и " if len(ahead) > 1 else "") + str(ahead[-1])
        parts.append(f"за {nums} {plural(ahead[-1], 'день', 'дня', 'дней')}")
    if 0 in days:
        parts.append("в день события")
    return " и ".join(parts) or "в день события"


def hashtag(text: str) -> str:
    return "#" + "".join(ch for ch in text.lower().replace(" ", "_") if ch.isalnum() or ch == "_")


# ───────────── события ─────────────

def event_caption(occ: Occurrence, today: dt.date, days_before: int | None = None) -> str:
    ev = occ.event
    cat_name, emoji = CATEGORIES.get(ev.category, ("Событие", "📌"))
    lines = [f"{emoji} <b>{esc(ev.name_ru)}</b> — {countdown(occ, today)}"]
    if ev.name_pt and ev.name_pt.lower() != ev.name_ru.lower():
        lines.append(f"<i>{esc(ev.name_pt)}</i>")
    lines.append("")
    lines.append(f"📅 {date_range(occ.start, occ.end, today)}")
    where = scope_label(ev.scope)
    lines.append(f"📍 {esc(where)}" + (f" · {esc(ev.place)}" if ev.place else ""))
    if ev.about:
        lines.append(f"ℹ️ {esc(ev.about)}")
    if ev.manager_tip:
        lines.append(f"💡 <b>Менеджерам:</b> {esc(ev.manager_tip)}")
    if ev.source == "news":
        lines.append("⚠️ <i>Событие найдено в новостях автоматически — дату стоит перепроверить.</i>")
    lines.append("")
    lines.append(f"#{'календарь'} {hashtag(cat_name)}")
    return _fit_caption("\n".join(lines))


def agenda_text(occs: list[Occurrence], today: dt.date, title: str) -> str:
    lines = [f"🗓 <b>{esc(title)}</b>", ""]
    for occ in occs:
        ev = occ.event
        _, emoji = CATEGORIES.get(ev.category, ("", "📌"))
        when = date_range(occ.start, occ.end, today)
        where = scope_label(ev.scope, short=True)
        lines.append(f"{emoji} <b>{esc(ev.name_ru)}</b> — {when}, {countdown(occ, today)}")
        lines.append(f"      📍 {esc(where)}")
    return "\n".join(lines)


# ───────────── новости ─────────────

def news_caption(item: dict, tz, breaking: bool, tz_note: str = "") -> str:
    cat_name, emoji = NEWS_CATEGORIES.get(item.get("category") or "other", NEWS_CATEGORIES["other"])
    head = f"⚡️ <b>СРОЧНО · {cat_name}</b>" if breaking else f"{emoji} <b>{cat_name}</b>"
    st = item.get("state") or "BR"
    if st not in ("BR", "INT"):
        head += f" · 📍 {esc(state_name(st))}"
    title = item.get("title_ru") or item.get("title")
    lines = [head, "", f"<b>{esc(title)}</b>"]
    if item.get("title_ru") and item.get("title") and item["title_ru"] != item["title"] and item.get("translated"):
        lines.append(f"<i>🇧🇷 {esc(item['title'])}</i>")
    if item.get("summary_ru"):
        lines.append(esc(item["summary_ru"]))
    when = ""
    if item.get("published_at"):
        t = dt.datetime.fromisoformat(item["published_at"]).astimezone(tz)
        when = f" · {t:%H:%M}" + (f" {tz_note}" if tz_note else "")
    src = esc(item.get("source") or "источник")
    link = f'<a href="{esc_attr(item["url"])}">{src}</a>' if item.get("url") else src
    lines.append("")
    lines.append(f"🔗 {link}{when}")
    lines.append(f"#новости {hashtag(cat_name)}")
    return _fit_caption("\n".join(lines))


def digest_text(items: list[dict], day: dt.date, with_summary: bool = True) -> str:
    lines = [f"📰 <b>Главное в Бразилии · {date_long(day, with_weekday=False)}</b>", ""]
    for i, it in enumerate(items, 1):
        cat_name, emoji = NEWS_CATEGORIES.get(it.get("category") or "other", NEWS_CATEGORIES["other"])
        title = esc(it.get("title_ru") or it.get("title"))
        link = f' <a href="{esc_attr(it["url"])}">→</a>' if it.get("url") else ""
        lines.append(f"{i}. {emoji} <b>{title}</b>{link}")
        if with_summary and it.get("summary_ru"):
            lines.append(f"<i>{esc(it['summary_ru'])}</i>")
        if with_summary:
            lines.append("")
    if not with_summary:
        lines.append("")
    lines.append("#дайджест")
    return "\n".join(lines).strip()


def _fit_caption(text: str, limit: int = 1024) -> str:
    """Подпись к фото в Telegram — максимум 1024 символа."""
    if len(text) <= limit:
        return text
    # режем по строкам с конца, сохраняя хэштег и ссылку
    lines = text.split("\n")
    while len("\n".join(lines)) > limit and len(lines) > 3:
        # удаляем самую длинную «описательную» строку
        idx = max(range(1, len(lines) - 2), key=lambda i: len(lines[i]))
        lines.pop(idx)
    return "\n".join(lines)[:limit]


def split_message(text: str, limit: int = 4000) -> list[str]:
    parts, cur = [], ""
    for block in text.split("\n"):
        if len(cur) + len(block) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += block + "\n"
    if cur.strip():
        parts.append(cur)
    return parts
