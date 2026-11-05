"""Хранилище на SQLite: пользователи, новости, отправленные напоминания."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .calendar_engine import Event
from .states import normalize_scope

USER_CATEGORIES = ["breaking", "digest", "holiday", "festival", "sport", "commercial"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    chat_id     INTEGER NOT NULL,
    name        TEXT,
    username    TEXT,
    states      TEXT NOT NULL DEFAULT '[]',
    categories  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    authorized  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_reminders (
    user_id  INTEGER NOT NULL,
    uid      TEXT NOT NULL,
    days_before INTEGER NOT NULL,
    sent_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, uid, days_before)
);
CREATE TABLE IF NOT EXISTS news (
    id            TEXT PRIMARY KEY,
    source        TEXT,
    topic         TEXT,
    url           TEXT,
    title         TEXT,
    summary       TEXT,
    image_url     TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    processed     INTEGER NOT NULL DEFAULT 0,
    importance    INTEGER,
    category      TEXT,
    state         TEXT,
    title_ru      TEXT,
    summary_ru    TEXT,
    is_duplicate  INTEGER NOT NULL DEFAULT 0,
    breaking_sent INTEGER NOT NULL DEFAULT 0,
    in_digest     TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_processed ON news(processed);
CREATE INDEX IF NOT EXISTS idx_news_fetched ON news(fetched_at);
CREATE TABLE IF NOT EXISTS custom_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name_ru     TEXT NOT NULL,
    name_pt     TEXT,
    category    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    importance  INTEGER NOT NULL DEFAULT 2,
    about       TEXT,
    tip         TEXT,
    place       TEXT,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    source      TEXT NOT NULL,
    news_url    TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_status (
    name        TEXT PRIMARY KEY,
    url         TEXT,
    last_ok     TEXT,
    last_error  TEXT,
    last_count  INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for col in ("cluster TEXT", "translated INTEGER NOT NULL DEFAULT 0", "outlets INTEGER"):
            try:
                self.conn.execute(f"ALTER TABLE news ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        self.conn.commit()

    # ───────────── users ─────────────
    def upsert_user(self, user_id: int, chat_id: int, name: str, username: str | None, authorized: bool) -> dict:
        row = self.get_user(user_id)
        if row:
            self.conn.execute(
                "UPDATE users SET chat_id=?, name=?, username=?, active=1, authorized=MAX(authorized, ?) WHERE user_id=?",
                (chat_id, name, username, int(authorized), user_id),
            )
        else:
            self.conn.execute(
                "INSERT INTO users (user_id, chat_id, name, username, states, categories, active, authorized, created_at)"
                " VALUES (?,?,?,?,?,?,1,?,?)",
                (user_id, chat_id, name, username, "[]", json.dumps(USER_CATEGORIES), int(authorized), utcnow().isoformat()),
            )
        self.conn.commit()
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return self._user(r) if r else None

    def _user(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["states"] = json.loads(d["states"] or "[]")
        d["categories"] = json.loads(d["categories"] or "[]")
        return d

    def active_users(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM users WHERE active=1 AND authorized=1").fetchall()
        return [self._user(r) for r in rows]

    def set_authorized(self, user_id: int) -> None:
        self.conn.execute("UPDATE users SET authorized=1 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def set_states(self, user_id: int, states: list[str]) -> None:
        self.conn.execute("UPDATE users SET states=? WHERE user_id=?", (json.dumps(states), user_id))
        self.conn.commit()

    def set_categories(self, user_id: int, cats: list[str]) -> None:
        self.conn.execute("UPDATE users SET categories=? WHERE user_id=?", (json.dumps(cats), user_id))
        self.conn.commit()

    def deactivate(self, user_id: int) -> None:
        self.conn.execute("UPDATE users SET active=0 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def user_counts(self) -> tuple[int, int]:
        total = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = self.conn.execute("SELECT COUNT(*) FROM users WHERE active=1 AND authorized=1").fetchone()[0]
        return total, active

    # ───────────── reminders ─────────────
    def reminder_sent(self, user_id: int, uid: str, offset: int) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM sent_reminders WHERE user_id=? AND uid=? AND days_before=?", (user_id, uid, offset)
        ).fetchone()
        return r is not None

    def mark_reminder(self, user_id: int, uid: str, offset: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sent_reminders (user_id, uid, days_before, sent_at) VALUES (?,?,?,?)",
            (user_id, uid, offset, utcnow().isoformat()),
        )
        self.conn.commit()

    # ───────────── news ─────────────
    def add_news(self, item: dict) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO news (id, source, topic, url, title, summary, image_url, published_at, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                item["id"], item["source"], item.get("topic"), item["url"], item["title"], item.get("summary", ""),
                item.get("image_url"), item.get("published_at"), utcnow().isoformat(),
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def news_total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]

    def recent_news(self, hours: int = 12) -> list[dict]:
        cutoff = (utcnow() - dt.timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM news WHERE fetched_at >= ? ORDER BY published_at", (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_news(self, news_id: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE news SET {cols} WHERE id=?", (*fields.values(), news_id))

    def commit(self) -> None:
        self.conn.commit()

    def mark_all_breaking_sent(self) -> None:
        self.conn.execute("UPDATE news SET breaking_sent=1")
        self.conn.commit()

    def mark_cluster_breaking_sent(self, cluster: str) -> None:
        self.conn.execute("UPDATE news SET breaking_sent=1 WHERE cluster=?", (cluster,))
        self.conn.commit()

    def unprocessed_news(self, limit: int = 60) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM news WHERE processed=0 ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_processed(self, news_id: str, **fields) -> None:
        fields["processed"] = 1
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE news SET {cols} WHERE id=?", (*fields.values(), news_id))
        self.conn.commit()

    def skip_old_unprocessed(self, older_than_hours: int = 12) -> None:
        cutoff = (utcnow() - dt.timedelta(hours=older_than_hours)).isoformat()
        self.conn.execute(
            "UPDATE news SET processed=1, importance=0 WHERE processed=0 AND published_at < ?", (cutoff,)
        )
        self.conn.commit()

    def recent_important_titles(self, hours: int = 36, min_importance: int = 5) -> list[str]:
        cutoff = (utcnow() - dt.timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT title_ru FROM news WHERE processed=1 AND is_duplicate=0 AND importance>=? AND fetched_at>=?"
            " AND title_ru IS NOT NULL ORDER BY importance DESC, fetched_at DESC LIMIT 60",
            (min_importance, cutoff),
        ).fetchall()
        return [r[0] for r in rows]

    def pending_breaking(self, threshold: int, hours: int = 6) -> list[dict]:
        cutoff = (utcnow() - dt.timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM news WHERE processed=1 AND is_duplicate=0 AND breaking_sent=0 AND importance>=?"
            " AND fetched_at>=? ORDER BY importance DESC, published_at ASC",
            (threshold, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_breaking_sent(self, news_id: str) -> None:
        self.conn.execute("UPDATE news SET breaking_sent=1 WHERE id=?", (news_id,))
        self.conn.commit()

    def digest_candidates(self, hours: int, min_importance: int) -> list[dict]:
        cutoff = (utcnow() - dt.timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM news WHERE processed=1 AND is_duplicate=0 AND importance>=? AND fetched_at>=?"
            " ORDER BY importance DESC, published_at DESC",
            (min_importance, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_news(self, limit: int = 8, min_importance: int = 5) -> list[dict]:
        cutoff = (utcnow() - dt.timedelta(hours=24)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM news WHERE processed=1 AND is_duplicate=0 AND importance>=? AND fetched_at>=?"
            " ORDER BY importance DESC, published_at DESC LIMIT ?",
            (min_importance, cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Сливает журнал WAL в основной файл — важно перед копированием базы."""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.commit()
        finally:
            self.conn.close()

    def cleanup(self, keep_days: int = 14) -> None:
        cutoff = (utcnow() - dt.timedelta(days=keep_days)).isoformat()
        self.conn.execute("DELETE FROM news WHERE fetched_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM sent_reminders WHERE sent_at < ?", ((utcnow() - dt.timedelta(days=400)).isoformat(),))
        self.conn.commit()
        self.conn.execute("VACUUM")

    def news_stats(self) -> dict:
        cutoff = (utcnow() - dt.timedelta(hours=24)).isoformat()
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "fetched_24h": q("SELECT COUNT(*) FROM news WHERE fetched_at>=?", cutoff),
            "important_24h": q("SELECT COUNT(*) FROM news WHERE fetched_at>=? AND importance>=5 AND is_duplicate=0", cutoff),
            "breaking_24h": q("SELECT COUNT(*) FROM news WHERE fetched_at>=? AND breaking_sent=1", cutoff),
        }

    # ───────────── custom events ─────────────
    def add_custom_event(self, ev: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO custom_events (name_ru, name_pt, category, scope, importance, about, tip, place, start_date, end_date,"
            " source, news_url, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
            (
                ev["name_ru"], ev.get("name_pt", ""), ev.get("category", "festival"), json.dumps(normalize_scope(ev.get("scope"))),
                int(ev.get("importance", 2)), ev.get("about", ""), ev.get("tip", ""), ev.get("place", ""),
                ev["start"], ev.get("end") or ev["start"], ev.get("source", "admin"), ev.get("news_url"),
                utcnow().isoformat(),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def custom_event_exists(self, name_ru: str, start: str) -> bool:
        rows = self.conn.execute("SELECT name_ru FROM custom_events WHERE start_date=? AND active=1", (start,)).fetchall()
        key = _norm(name_ru)
        return any(_similar(key, _norm(r[0])) for r in rows)

    def deactivate_custom_event(self, event_id: int) -> bool:
        cur = self.conn.execute("UPDATE custom_events SET active=0 WHERE id=?", (event_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def custom_events(self, include_past_days: int = 30) -> list[Event]:
        cutoff = (dt.date.today() - dt.timedelta(days=include_past_days)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM custom_events WHERE active=1 AND end_date >= ? ORDER BY start_date", (cutoff,)
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                Event(
                    id=f"custom{r['id']}",
                    name_ru=r["name_ru"],
                    name_pt=r["name_pt"] or "",
                    category=r["category"],
                    scope=json.loads(r["scope"]),
                    importance=r["importance"],
                    about=r["about"] or "",
                    tip=r["tip"] or "",
                    place=r["place"] or "",
                    rule={"date": r["start_date"], "end": r["end_date"]},
                    source=r["source"],
                    db_id=r["id"],
                )
            )
        return out

    # ───────────── feeds / kv ─────────────
    def feed_ok(self, name: str, url: str, count: int) -> None:
        self.conn.execute(
            "INSERT INTO feed_status (name, url, last_ok, last_error, last_count) VALUES (?,?,?,NULL,?)"
            " ON CONFLICT(name) DO UPDATE SET url=excluded.url, last_ok=excluded.last_ok, last_error=NULL,"
            " last_count=excluded.last_count",
            (name, url, utcnow().isoformat(), count),
        )
        self.conn.commit()

    def feed_error(self, name: str, url: str, error: str) -> None:
        self.conn.execute(
            "INSERT INTO feed_status (name, url, last_error) VALUES (?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET url=excluded.url, last_error=excluded.last_error",
            (name, url, error[:300]),
        )
        self.conn.commit()

    def feed_statuses(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM feed_status ORDER BY name").fetchall()]

    def kv_get(self, key: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def kv_add(self, key: str, amount: int) -> None:
        cur = int(self.kv_get(key, "0") or 0)
        self.kv_set(key, str(cur + amount))


def _norm(s: str) -> str:
    import re

    return re.sub(r"[^\w]+", " ", s.lower()).strip()


def _similar(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6
