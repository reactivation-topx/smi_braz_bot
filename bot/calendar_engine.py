"""Календарь событий: праздники, фестивали, спорт, коммерческие даты.

События описываются в data/events.yaml правилами, а не конкретными датами,
поэтому календарь сам продлевается на следующие годы:

    fixed: "07-09"                      — каждый год 9 июля
    date: "2026-11-28"                  — разовое событие (end: — дата окончания)
    easter: -47                         — относительно Пасхи (католической)
    nth_weekday: {month: 5, weekday: 6, n: 2}   — 2-е воскресенье мая (n: -1 = последнее)
    offset: 1                           — сдвиг в днях после вычисления даты
    duration: 3                         — длительность в днях (по умолчанию 1)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .states import NATIONAL, normalize_scope

CATEGORIES = {
    "holiday": ("Праздник", "🇧🇷"),
    "state_holiday": ("Праздник штата", "📍"),
    "festival": ("Фестиваль / шоу", "🎉"),
    "sport": ("Спорт", "⚽"),
    "commercial": ("Коммерческая дата", "🛍"),
    "culture": ("Культура", "🎭"),
}

DEFAULT_TIPS = {
    "holiday": "Выходной по всей стране: клиенты свободнее днём, многие уезжают на длинные выходные.",
    "state_holiday": "Выходной в штате: у клиентов оттуда больше свободного времени днём.",
    "festival": "Массовое событие — хороший повод для тематической активности и поздравлений.",
    "sport": "Пик интереса к спорту: подготовьте контент и предложения к матчу заранее.",
    "commercial": "Коммерческий пик: люди настроены тратить, конкуренция за внимание высокая.",
    "culture": "Заметный инфоповод — можно использовать в коммуникации с клиентами.",
}


def easter(year: int) -> dt.date:
    """Дата католической Пасхи (алгоритм Гаусса/Meeus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-й день недели месяца (weekday: 0=пн … 6=вс; n=-1 — последний)."""
    if n > 0:
        first = dt.date(year, month, 1)
        shift = (weekday - first.weekday()) % 7
        return first + dt.timedelta(days=shift + 7 * (n - 1))
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    shift = (last.weekday() - weekday) % 7
    return last - dt.timedelta(days=shift + 7 * (-n - 1))


@dataclass
class Event:
    id: str
    name_ru: str
    name_pt: str = ""
    category: str = "holiday"
    scope: list[str] = field(default_factory=lambda: [NATIONAL])
    importance: int = 2  # 1 — региональное, 2 — заметное, 3 — главное
    about: str = ""
    tip: str = ""
    place: str = ""
    rule: dict = field(default_factory=dict)
    duration: int = 1
    source: str = "calendar"  # calendar | admin | news
    db_id: int | None = None

    @property
    def is_national(self) -> bool:
        return NATIONAL in self.scope

    @property
    def manager_tip(self) -> str:
        return self.tip or DEFAULT_TIPS.get(self.category, "")

    def dates_in_year(self, year: int) -> list[tuple[dt.date, dt.date]]:
        r = self.rule
        start: dt.date | None = None
        end: dt.date | None = None
        if "date" in r:
            start = _parse_date(r["date"])
            end = _parse_date(r["end"]) if r.get("end") else None
            if start.year != year:
                return []
        elif "fixed" in r:
            mm, dd = str(r["fixed"]).split("-")
            start = dt.date(year, int(mm), int(dd))
        elif "easter" in r:
            start = easter(year) + dt.timedelta(days=int(r["easter"]))
        elif "nth_weekday" in r:
            nw = r["nth_weekday"]
            start = nth_weekday(year, int(nw["month"]), int(nw["weekday"]), int(nw["n"]))
        else:
            return []
        if r.get("offset"):
            start += dt.timedelta(days=int(r["offset"]))
        if end is None:
            end = start + dt.timedelta(days=max(1, self.duration) - 1)
        return [(start, end)]


@dataclass
class Occurrence:
    event: Event
    start: dt.date
    end: dt.date

    @property
    def uid(self) -> str:
        return f"{self.event.id}:{self.start.isoformat()}"

    def days_until(self, today: dt.date) -> int:
        return (self.start - today).days

    def is_multiday(self) -> bool:
        return self.end > self.start


def _parse_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def load_events(path: Path) -> list[Event]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    events: list[Event] = []
    seen: set[str] = set()
    for item in data:
        rule = {k: item[k] for k in ("fixed", "date", "end", "easter", "nth_weekday", "offset") if k in item}
        ev = Event(
            id=str(item["id"]),
            name_ru=item["name_ru"],
            name_pt=item.get("name_pt", ""),
            category=item.get("category", "holiday"),
            scope=normalize_scope(item.get("scope", "BR")),
            importance=int(item.get("importance", 2)),
            about=(item.get("about") or "").strip(),
            tip=(item.get("tip") or "").strip(),
            place=item.get("place", ""),
            rule=rule,
            duration=int(item.get("duration", 1)),
        )
        if ev.id in seen:
            raise ValueError(f"Повторяющийся id события в календаре: {ev.id}")
        seen.add(ev.id)
        events.append(ev)
    return events


def occurrences(events: list[Event], start: dt.date, end: dt.date) -> list[Occurrence]:
    """Все события, которые начинаются или идут в интервале [start, end]."""
    out: list[Occurrence] = []
    for ev in events:
        for year in range(start.year - 1, end.year + 1):
            for s, e in ev.dates_in_year(year):
                if e >= start and s <= end:
                    out.append(Occurrence(ev, s, e))
    out.sort(key=lambda o: (o.start, -o.event.importance, o.event.name_ru))
    return out


def category_toggle(category: str) -> str:
    """Какой переключатель в настройках отвечает за категорию события."""
    return {"state_holiday": "holiday", "culture": "festival"}.get(category, category)


def matches_user(ev: Event, user_states: list[str], user_categories: list[str]) -> bool:
    """Показывать ли событие пользователю с такими штатами и категориями."""
    if category_toggle(ev.category) not in user_categories:
        return False
    if ev.is_national:
        return True
    if "ALL" in user_states:
        return True
    return any(uf in user_states for uf in ev.scope)


def due_reminders(
    occs: list[Occurrence],
    today: dt.date,
    offsets: list[int],
) -> list[tuple[Occurrence, int]]:
    """Какие напоминания пора отправить сегодня.

    offsets — например [5, 3, 1, 0] (0 — в день события). Напоминание «за 5 дней» уходит, когда до события
    осталось 5 дней или меньше (но больше 3) — так бот догонит пропуски,
    если в нужный день он был выключен. Уже отправленные отсекаются в БД.
    """
    offsets = sorted(set(offsets), reverse=True)
    out = []
    for occ in occs:
        d = occ.days_until(today)
        if d < 0:
            continue
        for i, off in enumerate(offsets):
            lower = offsets[i + 1] if i + 1 < len(offsets) else -1
            if lower < d <= off:
                out.append((occ, off))
                break
    return out
