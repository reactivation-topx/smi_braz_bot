"""Ядро бота: рассылки по расписанию и общие помощники отправки."""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
import re

from . import cards, free_scoring, news, trends
from .ai import NewsAnalyzer
from .calendar_engine import Event, Occurrence, due_reminders, load_events, matches_user, occurrences
from .config import Settings
from .db import DB
from .translate import Translator
from .formatting import agenda_text, date_long, digest_text, event_caption, news_caption, split_message

log = logging.getLogger(__name__)


def tz_label(tz_name: str) -> str:
    if tz_name == "America/Sao_Paulo":
        return "по Бразилиа"
    return tz_name.split("/")[-1].replace("_", " ")


class App:
    def __init__(self, settings: Settings, bot):
        self.s = settings
        self.bot = bot
        self.db = DB(settings.db_path)
        self.analyzer = NewsAnalyzer(settings.anthropic_api_key, settings.claude_model, self.db)
        self.translator = Translator(settings.translate_provider, settings.translate_url, settings.translate_email)
        self.trends: list[dict] = []
        self.base_events: list[Event] = []
        self.sources: list[dict] = []
        self._news_lock = asyncio.Lock()
        self.reload()

    # ───────────── данные ─────────────
    def reload(self) -> tuple[int, int]:
        self.base_events = load_events(self.s.events_path)
        self.sources = news.load_sources(self.s.sources_path)
        return len(self.base_events), len(self.sources)

    def all_events(self) -> list[Event]:
        return self.base_events + self.db.custom_events()

    def today(self) -> dt.date:
        return dt.datetime.now(self.s.tz).date()

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.s.tz)

    def is_quiet(self) -> bool:
        if not self.s.quiet_hours:
            return False
        a, b = self.s.quiet_hours
        h = self.now().hour
        return (a <= h or h < b) if a > b else (a <= h < b)

    def user_occurrences(self, user: dict, days: int) -> list[Occurrence]:
        today = self.today()
        occs = occurrences(self.all_events(), today, today + dt.timedelta(days=days))
        return [o for o in occs if matches_user(o.event, user["states"], user["categories"])]

    # ───────────── отправка ─────────────
    async def send_text(self, chat_id: int, text: str, user_id: int | None = None, **kw) -> bool:
        ok = True
        for part in split_message(text):
            ok = await self._call(self.bot.send_message, chat_id, user_id, text=part, **kw) is not None and ok
        return ok

    async def send_photo(self, chat_id: int, photo, caption: str, user_id: int | None = None):
        """photo — bytes или file_id. Возвращает file_id отправленного фото (для повторного использования)."""
        from aiogram.types import BufferedInputFile

        media = BufferedInputFile(photo, filename="card.png") if isinstance(photo, (bytes, bytearray)) else photo
        msg = await self._call(self.bot.send_photo, chat_id, user_id, photo=media, caption=caption)
        if msg is not None and getattr(msg, "photo", None):
            return msg.photo[-1].file_id
        return None

    async def _call(self, method, chat_id: int, user_id: int | None, **kw):
        from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

        for attempt in range(3):
            try:
                res = await method(chat_id=chat_id, **kw)
                await asyncio.sleep(0.05)  # не упираемся в лимиты Telegram
                return res
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                log.info("Пользователь %s заблокировал бота — отключаем рассылку", chat_id)
                self.db.deactivate(user_id or chat_id)
                return None
            except TelegramBadRequest as e:
                if "can't parse" in str(e).lower() and kw.get("parse_mode", "HTML") is not None:
                    # сломанная разметка — отправляем тем же текстом без форматирования
                    for key in ("text", "caption"):
                        if kw.get(key):
                            kw[key] = html.unescape(re.sub(r"<[^>]+>", "", kw[key]))
                    kw["parse_mode"] = None
                    continue
                log.warning("Telegram отклонил сообщение для %s: %s", chat_id, e)
                return None
            except Exception as e:  # noqa: BLE001
                log.warning("Ошибка отправки %s (попытка %s): %s", chat_id, attempt + 1, e)
                await asyncio.sleep(2)
        return None

    async def notify_admins(self, text: str) -> None:
        for admin in self.s.admin_ids:
            await self.send_text(admin, text)

    # ───────────── новости ─────────────
    async def poll_news(self) -> None:
        if self._news_lock.locked():
            return
        async with self._news_lock:
            try:
                first_run = self.db.news_total() == 0
                added = await news.collect(self.db, self.sources)
                self.db.skip_old_unprocessed()
                log.info("Новости: добавлено %s новых", added)
                if self.analyzer.enabled:
                    for _ in range(4):  # до ~160 новостей за проход
                        batch = self.db.unprocessed_news(40)
                        if not batch:
                            break
                        await self._analyze_batch(batch)
                else:
                    if self.s.trends_url:
                        fresh = await trends.fetch_trends(self.s.trends_url)
                        self.trends = fresh or self.trends
                    self.free_rescore()
                if first_run:
                    # при первом запуске не засыпаем людей новостями за последние 12 часов
                    self.db.mark_all_breaking_sent()
            except Exception:
                log.exception("Ошибка при сборе новостей")
        await self.send_breaking()

    def free_rescore(self) -> None:
        """Бесплатный режим: склеить похожие новости и пересчитать важность каждой истории."""
        items = self.db.recent_news(hours=12)
        for group in free_scoring.cluster(items):
            sc = free_scoring.score_cluster(group, self.trends)
            rep = free_scoring.pick_representative(group)
            cl = rep["id"]
            for it in group:
                self.db.update_news(
                    it["id"], processed=1, importance=sc["importance"], category=sc["category"],
                    state=sc["state"], is_duplicate=int(it["id"] != rep["id"]), cluster=cl, outlets=sc["outlets"],
                )
        self.db.commit()

    async def ensure_translated(self, items: list[dict]) -> None:
        """Перевод заголовков и описаний для бесплатного режима — только того, что реально отправляем."""
        todo = [it for it in items if not it.get("title_ru") and not it.get("translated")]
        if not todo or not self.translator.enabled:
            return
        texts: list[str] = []
        for it in todo:
            texts.append(it["title"])
            texts.append(_first_sentence(it.get("summary") or ""))
        res = await self.translator.translate(texts)
        for i, it in enumerate(todo):
            title_ru, summary_ru = res[2 * i], res[2 * i + 1]
            if not title_ru:
                continue
            it["title_ru"], it["summary_ru"], it["translated"] = title_ru, summary_ru or "", 1
            self.db.update_news(it["id"], title_ru=title_ru, summary_ru=summary_ru or "", translated=1)
        self.db.commit()

    async def _analyze_batch(self, batch: list[dict]) -> None:
        already = self.db.recent_important_titles()
        try:
            results, events = await self.analyzer.analyze(batch, already, self.today())
        except Exception as e:  # noqa: BLE001
            log.error("Ошибка ИИ-анализа (%s) — повторим при следующем опросе", e)
            # не помечаем обработанными: повторим в следующий раз, а старые отсеются по времени
            if self.db.kv_get("ai_error_day") != self.today().isoformat():
                self.db.kv_set("ai_error_day", self.today().isoformat())
                await self.notify_admins(
                    "⚠️ Не получилось обработать новости через Claude:\n"
                    f"<code>{html.escape(str(e)[:300])}</code>\n"
                    "Проверьте ANTHROPIC_API_KEY и баланс в консоли Anthropic. Календарь при этом работает."
                )
            raise
        for r in results:
            nid = r.pop("id")
            self.db.mark_processed(nid, **r)
        if self.s.auto_events_from_news:
            for ev in events:
                if self.db.custom_event_exists(ev["name_ru"], ev["start"]):
                    continue
                if any(e.name_ru.lower() == ev["name_ru"].lower() for e in self.base_events):
                    continue
                eid = self.db.add_custom_event(ev)
                await self.notify_admins(
                    f"🆕 В календарь добавлено событие из новостей:\n<b>{html.escape(ev['name_ru'])}</b> — "
                    f"{ev['start']}{' – ' + ev['end'] if ev['end'] != ev['start'] else ''}, {html.escape(str(ev['scope']))}\n"
                    f"Если это ошибка: /del {eid}"
                )

    async def send_breaking(self) -> None:
        if self.is_quiet():
            return
        items = self.db.pending_breaking(self.s.breaking_threshold)[:5]
        if not items:
            return
        users = [u for u in self.db.active_users() if "breaking" in u["categories"]]
        for item in items:
            self.db.mark_breaking_sent(item["id"])  # сразу, чтобы не отправить дважды при сбое
            if item.get("cluster"):
                self.db.mark_cluster_breaking_sent(item["cluster"])
            recipients = [u for u in users if _news_for_user(item, u)]
            if not recipients:
                continue
            await self.ensure_translated([item])
            photo = await news.download_image(item.get("image_url"))
            png = await asyncio.to_thread(cards.news_card, item, self.s.tz, True, photo, tz_label(self.s.timezone_name))
            caption = news_caption(item, self.s.tz, True, tz_label(self.s.timezone_name))
            file_id = None
            for u in recipients:
                fid = await self.send_photo(u["chat_id"], file_id or png, caption, u["user_id"])
                file_id = file_id or fid

    async def daily_digest(self) -> None:
        cands = self.db.digest_candidates(24, self.s.digest_min_importance)
        if not cands:
            log.info("Дайджест: нет новостей")
            return
        today = self.today()
        cache: dict[tuple, str | bytes] = {}
        for u in self.db.active_users():
            if "digest" not in u["categories"]:
                continue
            items = [c for c in cands if _news_for_user(c, u)][: self.s.digest_size]
            if not items:
                continue
            await self.send_digest(u, items, today, cache)

    async def send_digest(self, user: dict, items: list[dict], day: dt.date, cache: dict | None = None) -> None:
        cache = {} if cache is None else cache
        await self.ensure_translated(items)
        key = tuple(i["id"] for i in items)
        png = cache.get(key) or await asyncio.to_thread(cards.digest_card, items, day)
        full = digest_text(items, day, with_summary=True)
        short = digest_text(items, day, with_summary=False)
        if len(full) <= 1024:
            fid = await self.send_photo(user["chat_id"], png, full, user["user_id"])
        elif len(short) <= 1024:
            fid = await self.send_photo(user["chat_id"], png, short, user["user_id"])
        else:
            fid = await self.send_photo(user["chat_id"], png, f"📰 <b>Главное в Бразилии · {date_long(day, with_weekday=False)}</b>", user["user_id"])
            await self.send_text(user["chat_id"], full, user["user_id"])
        if fid:
            cache[key] = fid

    # ───────────── календарь ─────────────
    async def daily_reminders(self, only_user: dict | None = None, dry_run: bool = False) -> int:
        today = self.today()
        horizon = max(self.s.remind_days) + 1
        occs = occurrences(self.all_events(), today, today + dt.timedelta(days=horizon))
        due = due_reminders(occs, today, self.s.remind_days)
        if not due:
            return 0
        users = [only_user] if only_user else self.db.active_users()
        card_cache: dict[str, bytes | str] = {}
        sent = 0
        for u in users:
            mine = [
                (o, off) for o, off in due
                if matches_user(o.event, u["states"], u["categories"])
                and (dry_run or not self.db.reminder_sent(u["user_id"], o.uid, off))
            ]
            if not mine:
                continue
            mine.sort(key=lambda x: (x[0].start, -x[0].event.importance))
            if len(mine) <= 3:
                for occ, off in mine:
                    key = f"{occ.uid}:{today}"
                    png = card_cache.get(key) or await asyncio.to_thread(cards.event_card, occ, today)
                    fid = await self.send_photo(u["chat_id"], png, event_caption(occ, today), u["user_id"])
                    if fid:
                        card_cache[key] = fid
                        sent += 1
            else:
                occs_u = [o for o, _ in mine]
                png = await asyncio.to_thread(
                    cards.agenda_card, occs_u, today, "Скоро в Бразилии", "Напоминание: готовимся заранее"
                )
                text = agenda_text(occs_u, today, "Скоро в Бразилии — готовимся заранее")
                if len(text) <= 1024:
                    await self.send_photo(u["chat_id"], png, text, u["user_id"])
                else:
                    await self.send_photo(u["chat_id"], png, "🗓 <b>Скоро в Бразилии</b>", u["user_id"])
                    await self.send_text(u["chat_id"], text, u["user_id"])
                # самые важные — ещё и отдельной карточкой с подсказкой менеджерам
                for occ, _ in [m for m in mine if m[0].event.importance >= 3][:2]:
                    key = f"{occ.uid}:{today}"
                    png1 = card_cache.get(key) or await asyncio.to_thread(cards.event_card, occ, today)
                    fid = await self.send_photo(u["chat_id"], png1, event_caption(occ, today), u["user_id"])
                    if fid:
                        card_cache[key] = fid
                sent += len(mine)
            if not dry_run:
                for occ, off in mine:
                    self.db.mark_reminder(u["user_id"], occ.uid, off)
        return sent

    async def send_agenda(self, user: dict, days: int, title: str) -> None:
        occs = self.user_occurrences(user, days)
        today = self.today()
        if not occs:
            await self.send_text(user["chat_id"], f"🗓 {title}: событий по вашим настройкам нет.", user["user_id"])
            return
        top = sorted(occs, key=lambda o: (o.start, -o.event.importance))
        png = await asyncio.to_thread(
            cards.agenda_card, top[:7], today, title,
            f"{len(occs)} {_events_word(len(occs))}" + (" · на карточке первые 7" if len(occs) > 7 else ""),
        )
        await self.send_photo(user["chat_id"], png, f"🗓 <b>{title}</b>", user["user_id"])
        await self.send_text(user["chat_id"], agenda_text(top, today, title), user["user_id"])

    async def weekly_overview(self) -> None:
        for u in self.db.active_users():
            if not self.user_occurrences(u, 7):
                continue
            await self.send_agenda(u, 7, "Неделя в Бразилии")

    async def cleanup(self) -> None:
        self.db.cleanup(keep_days=self.s.keep_news_days)

    async def run_due_tasks(self) -> None:
        """Ежедневные рассылки «по факту»: если время пришло, а сегодня ещё не отправляли — отправить.

        Так рассылки не теряются, даже если бот был выключен ровно в нужную минуту
        (и работает в режиме коротких запусков по расписанию, например на GitHub Actions).
        """
        now = self.now()
        today = now.date().isoformat()
        hm = (now.hour, now.minute)
        try:
            if hm >= self.s.digest_time and self.db.kv_get("last_digest") != today:
                self.db.kv_set("last_digest", today)
                await self.daily_digest()
            if (now.weekday() == self.s.weekly_day and hm >= self.s.remind_time
                    and self.db.kv_get("last_weekly") != today):
                self.db.kv_set("last_weekly", today)
                await self.weekly_overview()
            if hm >= self.s.remind_time and self.db.kv_get("last_reminders") != today:
                await self.daily_reminders()  # повторный запуск безопасен: дубли отсекаются в БД
                self.db.kv_set("last_reminders", today)
            if self.db.kv_get("last_cleanup") != today:
                self.db.kv_set("last_cleanup", today)
                await self.cleanup()
        except Exception:
            log.exception("Ошибка ежедневной рассылки")


def _first_sentence(text: str, limit: int = 220) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= limit else s[: limit].rsplit(" ", 1)[0] + "…"


def _news_for_user(item: dict, user: dict) -> bool:
    st = item.get("state") or "BR"
    if st in ("BR", "INT"):
        return True
    if "ALL" in user["states"]:
        return True
    if st in user["states"]:
        return True
    # региональная новость, но очень громкая — всем
    return int(item.get("importance") or 0) >= 9


def _events_word(n: int) -> str:
    from .formatting import plural

    return plural(n, "событие", "события", "событий")
