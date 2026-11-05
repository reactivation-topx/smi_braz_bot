"""Настройки бота. Всё берётся из переменных окружения (файл .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env, чтобы не тянуть лишнюю зависимость."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _int_list(value: str) -> list[int]:
    out = []
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def _hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = value.strip().split(":")
        return int(h), int(m)
    except Exception:
        return default


@dataclass
class Settings:
    bot_token: str = ""
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    admin_ids: list[int] = field(default_factory=list)
    access_code: str = ""
    timezone_name: str = "America/Sao_Paulo"
    remind_days: list[int] = field(default_factory=lambda: [5, 3, 1, 0])
    remind_time: tuple[int, int] = (10, 0)
    digest_time: tuple[int, int] = (9, 0)
    weekly_day: int = 0  # 0 = понедельник
    news_interval_min: int = 10
    breaking_threshold: int = 8
    digest_min_importance: int = 5
    digest_size: int = 8
    quiet_hours: tuple[int, int] | None = None
    auto_events_from_news: bool = True
    keep_news_days: int = 14
    translate_provider: str = "mymemory"
    translate_email: str = ""
    translate_url: str = ""
    trends_url: str = "https://trends.google.com/trending/rss?geo=BR"
    db_path: Path = ROOT / "data" / "bot.db"
    events_path: Path = ROOT / "data" / "events.yaml"
    sources_path: Path = ROOT / "data" / "sources.yaml"
    fonts_dir: Path = ROOT / "assets" / "fonts"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    env = os.environ.get
    quiet = env("QUIET_HOURS", "off").strip()
    quiet_hours = None
    if quiet and quiet.lower() not in {"off", "0", "no", "нет"}:
        try:
            a, b = quiet.split("-")
            quiet_hours = (int(a), int(b))
        except ValueError:
            quiet_hours = None
    s = Settings(
        bot_token=env("BOT_TOKEN", ""),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        claude_model=env("CLAUDE_MODEL", "claude-haiku-5-5"),
        admin_ids=_int_list(env("ADMIN_IDS", "")),
        access_code=env("ACCESS_CODE", "").strip(),
        timezone_name=env("TIMEZONE", "America/Sao_Paulo"),
        remind_days=sorted(set(_int_list(env("REMIND_DAYS", "5,3,1,0"))), reverse=True) or [5, 3, 1, 0],
        remind_time=_hhmm(env("REMIND_TIME", "10:00"), (10, 0)),
        digest_time=_hhmm(env("DIGEST_TIME", "09:00"), (9, 0)),
        weekly_day=int(env("WEEKLY_DAY", "0")),
        news_interval_min=max(5, int(env("NEWS_INTERVAL_MIN", "10"))),
        breaking_threshold=int(env("BREAKING_THRESHOLD", "8")),
        digest_min_importance=int(env("DIGEST_MIN_IMPORTANCE", "5")),
        digest_size=int(env("DIGEST_SIZE", "8")),
        quiet_hours=quiet_hours,
        auto_events_from_news=env("AUTO_EVENTS_FROM_NEWS", "1").lower() in {"1", "true", "yes", "да"},
        keep_news_days=int(env("KEEP_NEWS_DAYS", "14")),
        translate_provider=env("TRANSLATE_PROVIDER", "mymemory").strip().lower(),
        translate_email=env("TRANSLATE_EMAIL", "").strip(),
        translate_url=env("TRANSLATE_URL", "").strip(),
        trends_url=env("TRENDS_URL", "https://trends.google.com/trending/rss?geo=BR").strip(),
    )
    if s.trends_url.lower() in {"off", "0", "no", "нет"}:
        s.trends_url = ""
    if env("DB_PATH"):
        s.db_path = Path(env("DB_PATH"))
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    return s
