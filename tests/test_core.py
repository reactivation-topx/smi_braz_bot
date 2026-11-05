"""Проверка логики без Telegram: python -m unittest discover -s tests"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import cards  # noqa: E402
from bot.ai import NewsAnalyzer, heuristic  # noqa: E402
from bot.app import App, _news_for_user  # noqa: E402
from bot.calendar_engine import (  # noqa: E402
    due_reminders, easter, load_events, matches_user, nth_weekday, occurrences,
)
from bot.config import Settings  # noqa: E402
from bot.db import USER_CATEGORIES  # noqa: E402
from bot.formatting import digest_text, event_caption, news_caption  # noqa: E402
from bot.news import news_id, parse_feed  # noqa: E402

EVENTS = load_events(ROOT / "data" / "events.yaml")
TZ = ZoneInfo("America/Sao_Paulo")


def occ_by_id(eid: str, year_from: dt.date):
    return next(o for o in occurrences(EVENTS, year_from, year_from + dt.timedelta(days=400)) if o.event.id == eid)


class CalendarTests(unittest.TestCase):
    def test_easter(self):
        self.assertEqual(easter(2026), dt.date(2026, 4, 5))
        self.assertEqual(easter(2027), dt.date(2027, 3, 28))
        self.assertEqual(easter(2028), dt.date(2028, 4, 16))

    def test_movable_dates(self):
        d = dt.date(2026, 11, 5)
        carn = occ_by_id("carnaval", d)
        self.assertEqual((carn.start, carn.end), (dt.date(2027, 2, 5), dt.date(2027, 2, 10)))
        self.assertEqual(occ_by_id("sexta_santa", d).start, dt.date(2027, 3, 26))
        self.assertEqual(occ_by_id("corpus_christi", d).start, dt.date(2027, 5, 27))
        self.assertEqual(occ_by_id("black_friday", d).start, dt.date(2026, 11, 27))
        self.assertEqual(occ_by_id("dia_maes", d).start, dt.date(2027, 5, 9))
        self.assertEqual(occ_by_id("dia_pais", d).start, dt.date(2027, 8, 8))
        self.assertEqual(occ_by_id("cirio_nazare", d).start, dt.date(2027, 10, 10))
        p = occ_by_id("parintins", d)
        self.assertEqual((p.start, p.end), (dt.date(2027, 6, 25), dt.date(2027, 6, 27)))

    def test_nth_weekday_last(self):
        self.assertEqual(nth_weekday(2026, 6, 4, -1), dt.date(2026, 6, 26))
        self.assertEqual(nth_weekday(2026, 12, 6, -1), dt.date(2026, 12, 27))

    def test_one_off_and_ids_unique(self):
        ids = [e.id for e in EVENTS]
        self.assertEqual(len(ids), len(set(ids)))
        f1 = occ_by_id("f1_sao_paulo_2026", dt.date(2026, 11, 1))
        self.assertEqual((f1.start, f1.end), (dt.date(2026, 11, 6), dt.date(2026, 11, 8)))
        # разовое событие не повторяется на следующий год
        later = occurrences(EVENTS, dt.date(2027, 6, 1), dt.date(2028, 6, 1))
        self.assertFalse(any(o.event.id == "f1_sao_paulo_2026" for o in later))

    def test_due_reminders_5_and_3(self):
        today = dt.date(2026, 11, 5)
        bf = occ_by_id("black_friday", today)  # 27.11
        for day, expected in [(21, None), (22, 5), (23, 5), (24, 3), (26, 3), (27, 3), (28, None)]:
            res = due_reminders([bf], dt.date(2026, 11, day), [5, 3])
            got = res[0][1] if res else None
            self.assertEqual(got, expected, f"{day} ноября")

    def test_due_reminders_with_day_before_and_same_day(self):
        bf = occ_by_id("black_friday", dt.date(2026, 11, 5))  # 27.11
        offs = [5, 3, 1, 0]
        got = {day: (due_reminders([bf], dt.date(2026, 11, day), offs) or [(None, None)])[0][1]
               for day in range(21, 29)}
        self.assertEqual(got, {21: None, 22: 5, 23: 5, 24: 3, 25: 3, 26: 1, 27: 0, 28: None})

    def test_schedule_text(self):
        from bot.formatting import remind_schedule
        self.assertEqual(remind_schedule([5, 3, 1, 0]), "за 5, 3 и 1 день и в день события")
        self.assertEqual(remind_schedule([5, 3]), "за 5 и 3 дня")

    def test_user_filter(self):
        rs = next(e for e in EVENTS if e.id == "rs_farroupilha")
        natal = next(e for e in EVENTS if e.id == "natal")
        cats = list(USER_CATEGORIES)
        self.assertTrue(matches_user(natal, [], cats))
        self.assertFalse(matches_user(rs, ["SP"], cats))
        self.assertTrue(matches_user(rs, ["SP", "RS"], cats))
        self.assertTrue(matches_user(rs, ["ALL"], cats))
        self.assertFalse(matches_user(rs, ["RS"], ["sport"]))


class FeedTests(unittest.TestCase):
    RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>g1</title>
<item><title><![CDATA[Neymar anuncia aposentadoria da sele\xc3\xa7\xc3\xa3o]]></title>
<link>https://g1.globo.com/a.ghtml</link>
<description><![CDATA[<img src="https://s2.glbimg.com/x.jpg" /><br />Atacante fez o an\xc3\xbancio nas redes.]]></description>
<pubDate>Thu, 05 Nov 2026 14:32:00 -0300</pubDate></item>
<item><title>Receita de bolo</title><link>https://g1.globo.com/b</link>
<media:content url="https://img/b.jpg" medium="image"/>
<pubDate>Thu, 05 Nov 2026 10:00:00 -0300</pubDate></item>
</channel></rss>"""
    GNEWS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Lula sanciona lei - Folha de S.Paulo</title><link>https://news.google.com/rss/articles/abc</link>
<pubDate>Thu, 05 Nov 2026 17:00:00 GMT</pubDate><description>&lt;a href="x"&gt;Lula sanciona lei&lt;/a&gt;</description>
<source url="https://www.folha.uol.com.br">Folha de S.Paulo</source></item></channel></rss>"""
    ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom title</title><link href="https://x/1"/><updated>2026-11-05T12:00:00Z</updated>
<summary>Resumo</summary></entry></feed>"""

    def test_rss(self):
        items = parse_feed(self.RSS, {"name": "g1", "url": "https://g1.globo.com/rss/g1/", "topic": "general"})
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["image_url"], "https://s2.glbimg.com/x.jpg")
        self.assertIn("Atacante fez", items[0]["summary"])
        self.assertEqual(items[0]["published_at"], "2026-11-05T17:32:00+00:00")
        self.assertEqual(items[1]["image_url"], "https://img/b.jpg")

    def test_google_news(self):
        items = parse_feed(self.GNEWS, {"name": "Google", "url": "https://news.google.com/rss?x", "topic": "general"})
        self.assertEqual(items[0]["title"], "Lula sanciona lei")
        self.assertEqual(items[0]["source"], "Folha de S.Paulo")
        self.assertEqual(items[0]["summary"], "")

    def test_atom(self):
        items = parse_feed(self.ATOM, {"name": "A", "url": "https://a", "topic": "general"})
        self.assertEqual(items[0]["url"], "https://x/1")

    def test_same_story_same_id(self):
        self.assertEqual(news_id("Neymar anuncia aposentadoria!"), news_id("neymar anuncia  aposentadoria"))


class FakeBlock(SimpleNamespace):
    pass


class AnalyzerTests(unittest.TestCase):
    def test_parse_claude_tool_output(self):
        an = NewsAnalyzer("", "claude-haiku-5-5")
        items = [
            {"id": "a", "title": "Neymar anuncia aposentadoria", "source": "g1", "topic": "sport", "url": "u1"},
            {"id": "b", "title": "Show da Anitta no Rio em 20 de dezembro", "source": "g1", "topic": "general", "url": "u2"},
        ]
        tool_input = {
            "items": [
                {"n": 1, "importance": 9, "category": "sport", "state": "BR", "title_ru": "Неймар уходит из сборной",
                 "summary_ru": "Кратко.", "duplicate": False},
                {"n": 2, "importance": 6, "category": "showbiz", "state": "rj", "title_ru": "Концерт Анитты в Рио",
                 "summary_ru": "Кратко.", "duplicate": False},
            ],
            "events": [
                {"n": 2, "name_ru": "Концерт Анитты", "start": "2026-12-20", "category": "culture", "scope": "RJ",
                 "about": "Концерт."},
                {"n": 2, "name_ru": "Прошедшее", "start": "2020-01-01", "category": "culture", "scope": "RJ", "about": "x"},
            ],
        }

        class FakeMessages:
            async def create(self, **kw):
                assert kw["tool_choice"]["name"] == "report_news"
                return SimpleNamespace(content=[FakeBlock(type="tool_use", input=tool_input)],
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=50))

        an.client = SimpleNamespace(messages=FakeMessages())
        res, events = asyncio.run(an.analyze(items, [], dt.date(2026, 11, 5)))
        self.assertEqual(res[0]["importance"], 9)
        self.assertEqual(res[1]["state"], "RJ")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["news_url"], "u2")

    def test_heuristic(self):
        r = heuristic([
            {"id": "1", "title": "Morre cantor famoso; Anitta lamenta", "topic": "entertainment"},
            {"id": "2", "title": "Receita de bolo de cenoura", "topic": "general"},
        ])
        self.assertGreaterEqual(r[0]["importance"], 8)
        self.assertLessEqual(r[1]["importance"], 2)


class FormattingAndCardsTests(unittest.TestCase):
    def test_captions_fit_telegram(self):
        today = dt.date(2026, 11, 5)
        for o in occurrences(EVENTS, today, today + dt.timedelta(days=400)):
            cap = event_caption(o, today)
            self.assertLessEqual(len(cap), 1024, o.event.id)
            self.assertNotIn("<b><b>", cap)
        item = {"category": "sport", "state": "SP", "title_ru": "Заголовок <тест> & ко", "summary_ru": "x" * 900,
                "source": "g1", "url": "https://a.b/?q=1&x=\"2\"", "published_at": "2026-11-05T17:32:00+00:00"}
        cap = news_caption(item, TZ, True, "по Бразилиа")
        self.assertLessEqual(len(cap), 1024)
        self.assertIn("&lt;тест&gt;", cap)
        self.assertIn("&quot;2&quot;", cap)
        self.assertIn("14:32", cap)

    def test_cards_render(self):
        today = dt.date(2026, 11, 5)
        for eid in ("carnaval", "libertadores_final_2026", "rs_farroupilha", "the_town_2027", "womens_world_cup_2027"):
            png = cards.event_card(occ_by_id(eid, today), today)
            self.assertTrue(png.startswith(b"\x89PNG"))
        long_title = "Очень длинный заголовок новости " * 8
        png = cards.news_card({"title_ru": long_title, "summary_ru": long_title, "category": "other",
                               "importance": 7, "source": "g1", "published_at": "2026-11-05T17:32:00+00:00"},
                              TZ, False, b"not an image")
        self.assertTrue(png.startswith(b"\x89PNG"))
        occs = occurrences(EVENTS, today, today + dt.timedelta(days=30))
        self.assertTrue(cards.agenda_card(occs, today, "Тест").startswith(b"\x89PNG"))
        self.assertTrue(cards.digest_card([{"title_ru": long_title}], today).startswith(b"\x89PNG"))


class AppFlowTests(unittest.TestCase):
    def make_app(self, today: dt.date) -> tuple[App, list]:
        tmp = tempfile.mkdtemp()
        s = Settings(db_path=Path(tmp) / "t.db", events_path=ROOT / "data" / "events.yaml",
                     sources_path=ROOT / "data" / "sources.yaml", quiet_hours=None)
        app = App(s, bot=None)
        app.today = lambda: today  # type: ignore[assignment]
        sent: list = []

        async def fake_photo(chat_id, photo, caption, user_id=None):
            sent.append(("photo", chat_id, caption))
            return "file-id"

        async def fake_text(chat_id, text, user_id=None, **kw):
            sent.append(("text", chat_id, text))
            return True

        app.send_photo = fake_photo  # type: ignore[assignment]
        app.send_text = fake_text  # type: ignore[assignment]
        return app, sent

    def test_reminders_sent_once_per_offset(self):
        app, sent = self.make_app(dt.date(2026, 11, 23))  # Либертадорес 28.11 → за 5 дней
        app.db.upsert_user(1, 1, "SP менеджер", None, True)
        app.db.set_states(1, ["SP"])
        app.db.upsert_user(2, 2, "RS менеджер", None, True)
        app.db.set_states(2, ["RS"])
        asyncio.run(app.daily_reminders())
        first = [c for _, chat, c in sent if chat == 1]
        self.assertTrue(any("Либертадорес" in c for c in first))
        self.assertTrue(any("через 5 дней" in c for c in first))
        # Santa Catarina 25.11 (за 2 дня) — только тем, кто выбрал SC
        self.assertFalse(any("Екатерины" in c for c in first))
        n = len(sent)
        asyncio.run(app.daily_reminders())
        self.assertEqual(len(sent), n, "повторный запуск в тот же день не должен дублировать")
        app.today = lambda: dt.date(2026, 11, 25)  # type: ignore[assignment]
        asyncio.run(app.daily_reminders())
        third = [c for _, chat, c in sent[n:] if chat == 1]
        self.assertTrue(any("Либертадорес" in c and "через 3 дня" in c for c in third))
        for day, label in [(27, "завтра"), (28, "сегодня")]:
            m = len(sent)
            app.today = lambda d=day: dt.date(2026, 11, d)  # type: ignore[assignment]
            asyncio.run(app.daily_reminders())
            got = [c for _, chat, c in sent[m:] if chat == 1]
            self.assertTrue(any("Либертадорес" in c and label in c for c in got), label)
            asyncio.run(app.daily_reminders())
            self.assertEqual(len([1 for _, chat, _c in sent[m:] if chat == 1]), len(got), "без дублей")

    def test_many_reminders_grouped(self):
        app, sent = self.make_app(dt.date(2027, 2, 2))
        app.db.upsert_user(1, 1, "all", None, True)
        app.db.set_states(1, ["ALL"])
        app.db.add_custom_event({"name_ru": "Тест 1", "start": "2027-02-05", "scope": "BR", "category": "sport"})
        app.db.add_custom_event({"name_ru": "Тест 2", "start": "2027-02-06", "scope": "BR", "category": "sport"})
        app.db.add_custom_event({"name_ru": "Тест 3", "start": "2027-02-06", "scope": "BR", "category": "festival"})
        asyncio.run(app.daily_reminders())
        self.assertTrue(any("Скоро в Бразилии" in c for _, _, c in sent))
        self.assertTrue(any("Карнавал" in c for _, _, c in sent))

    def test_breaking_and_digest_routing(self):
        app, sent = self.make_app(dt.date(2026, 11, 5))
        app.db.upsert_user(1, 1, "SP", None, True)
        app.db.set_states(1, ["SP"])
        app.db.upsert_user(2, 2, "RJ", None, True)
        app.db.set_states(2, ["RJ"])
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for nid, title, state, imp in [("n1", "Национальная новость", "BR", 9), ("n2", "Новость Рио", "RJ", 8),
                                       ("n3", "Мелочь", "BR", 3)]:
            app.db.add_news({"id": nid, "source": "g1", "url": f"https://x/{nid}", "title": title,
                             "published_at": now})
            app.db.mark_processed(nid, importance=imp, category="other", state=state, title_ru=title,
                                  summary_ru="", is_duplicate=0)
        asyncio.run(app.send_breaking())
        to1 = [c for _, chat, c in sent if chat == 1]
        to2 = [c for _, chat, c in sent if chat == 2]
        self.assertEqual(len(to1), 1)
        self.assertEqual(len(to2), 2)
        asyncio.run(app.send_breaking())
        self.assertEqual(len(sent), 3, "срочная новость уходит один раз")
        sent.clear()
        asyncio.run(app.daily_digest())
        self.assertTrue(all("Мелочь" not in c for _, _, c in sent))
        self.assertTrue(any("Новость Рио" in c for _, chat, c in sent if chat == 2))
        self.assertFalse(any("Новость Рио" in c for _, chat, c in sent if chat == 1))

    def test_regional_news_filter(self):
        self.assertTrue(_news_for_user({"state": "BR"}, {"states": []}))
        self.assertFalse(_news_for_user({"state": "RJ", "importance": 7}, {"states": ["SP"]}))
        self.assertTrue(_news_for_user({"state": "RJ", "importance": 10}, {"states": ["SP"]}))

    def test_digest_text_short(self):
        items = [{"title_ru": f"Новость {i}", "summary_ru": "Пояснение " * 20, "url": "https://x"} for i in range(8)]
        self.assertLess(len(digest_text(items, dt.date(2026, 11, 5), with_summary=False)), 1024)


class DueTasksTests(unittest.TestCase):
    def test_daily_tasks_run_once_per_day(self):
        app, sent = AppFlowTests().make_app(dt.date(2027, 2, 4))  # карнавал 5.02 → «завтра»
        app.db.upsert_user(1, 1, "u", None, True)
        calls = []
        orig_digest = app.daily_digest

        async def digest():
            calls.append("digest")
            await orig_digest()

        app.daily_digest = digest  # type: ignore[assignment]
        times = iter([dt.datetime(2027, 2, 4, 8, 0, tzinfo=TZ), dt.datetime(2027, 2, 4, 10, 5, tzinfo=TZ),
                      dt.datetime(2027, 2, 4, 10, 15, tzinfo=TZ)])
        app.now = lambda: next(times)  # type: ignore[assignment]
        asyncio.run(app.run_due_tasks())       # 8:00 — ещё рано
        self.assertEqual(calls, [])
        asyncio.run(app.run_due_tasks())       # 10:05 — дайджест + напоминания
        n = len(sent)
        self.assertEqual(calls, ["digest"])
        self.assertTrue(any("Карнавал" in c for _, _, c in sent))
        asyncio.run(app.run_due_tasks())       # 10:15 — ничего повторно
        self.assertEqual(calls, ["digest"])
        self.assertEqual(len(sent), n)
        app.db.close()


class FreeModeTests(unittest.TestCase):
    TRENDS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>Neymar</title><ht:approx_traffic>500.000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Neymar anuncia aposentadoria</ht:news_item_title></ht:news_item></item>
<item><title>previs\xc3\xa3o do tempo</title><ht:approx_traffic>20 mil+</ht:approx_traffic></item>
</channel></rss>"""

    def test_trends(self):
        from bot.trends import parse_traffic, parse_trends
        tr = parse_trends(self.TRENDS)
        self.assertEqual(tr[0]["term"], "Neymar")
        self.assertEqual(tr[0]["traffic"], 500_000)
        self.assertEqual(tr[1]["traffic"], 20_000)
        self.assertEqual(parse_traffic("2M+"), 2_000_000)
        self.assertEqual(parse_traffic("10K+"), 10_000)

    def test_cluster_and_score(self):
        from bot import free_scoring as fs
        story = [
            ("g1", "Neymar anuncia aposentadoria da Seleção Brasileira"),
            ("Folha de S.Paulo", "Neymar anuncia aposentadoria da seleção após 15 anos"),
            ("UOL", "Aposentadoria de Neymar da Seleção: veja a repercussão"),
            ("CNN Brasil", "Neymar confirma aposentadoria da seleção brasileira"),
            ("ge", "Neymar se despede da Seleção e anuncia aposentadoria"),
        ]
        other = [("g1", "Receita de bolo de cenoura fácil"), ("Metrópoles", "Chuvas no Rio Grande do Sul deixam cidades em alerta")]
        items = [{"id": f"s{i}", "source": src, "title": t, "topic": "general", "published_at": f"2026-11-05T1{i}:00"}
                 for i, (src, t) in enumerate(story + other)]
        groups = fs.cluster(items)
        big = max(groups, key=len)
        self.assertEqual(len(big), 5)
        trends = [{"term": "Neymar", "traffic": 500_000}]
        sc = fs.score_cluster(big, trends)
        self.assertGreaterEqual(sc["importance"], 9)
        self.assertEqual(sc["category"], "sport")
        cake = next(g for g in groups if "bolo" in g[0]["title"])
        self.assertLessEqual(fs.score_cluster(cake, trends)["importance"], 2)
        rain = next(g for g in groups if "Chuvas" in g[0]["title"])
        rs = fs.score_cluster(rain, trends)
        self.assertEqual((rs["state"], rs["category"]), ("RS", "weather"))

    def test_free_pipeline_end_to_end(self):
        app, sent = AppFlowTests().make_app(dt.date(2026, 11, 5))
        app.db.upsert_user(1, 1, "u", None, True)
        app.trends = [{"term": "Neymar", "traffic": 500_000}]

        class FakeTranslator:
            enabled = True
            calls = 0

            async def translate(self, texts):
                FakeTranslator.calls += 1
                return [("RU: " + t) if t else None for t in texts]

        app.translator = FakeTranslator()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        titles = ["Neymar anuncia aposentadoria da Seleção Brasileira",
                  "Neymar anuncia aposentadoria da seleção após 15 anos",
                  "Neymar confirma aposentadoria da seleção brasileira",
                  "Neymar se despede da Seleção e anuncia aposentadoria",
                  "Receita de bolo de cenoura fácil"]
        for i, t in enumerate(titles):
            app.db.add_news({"id": f"n{i}", "source": f"src{i}", "url": f"https://x/{i}", "title": t,
                             "summary": "Atacante fez o anúncio. Mais detalhes.", "published_at": now})
        app.free_rescore()
        asyncio.run(app.send_breaking())
        self.assertEqual(len(sent), 1, "одна история — одно срочное сообщение")
        self.assertIn("RU: Neymar", sent[0][2])
        self.assertIn("🇧🇷", sent[0][2])
        # новое издание подхватило ту же историю — повтора быть не должно
        app.db.add_news({"id": "n9", "source": "src9", "url": "https://x/9",
                         "title": "Neymar anuncia aposentadoria e emociona torcedores da Seleção", "published_at": now})
        app.free_rescore()
        asyncio.run(app.send_breaking())
        self.assertEqual(len(sent), 1)
        sent.clear()
        asyncio.run(app.daily_digest())
        self.assertTrue(sent and "Neymar" in sent[0][2])
        self.assertNotIn("bolo", sent[0][2])

    def test_translator_guards(self):
        from bot.translate import Translator, _clip_bytes
        self.assertLessEqual(len(_clip_bytes("ação " * 200, 480).encode()), 480)
        self.assertEqual(_clip_bytes("curto", 480), "curto")
        t = Translator("mymemory", email="x@example.com")
        self.assertTrue(t.enabled)
        t._blocked_until = dt.date.today()  # лимит исчерпан → сеть не трогаем, возвращаем оригиналы
        self.assertEqual(asyncio.run(t.translate(["a", "b"])), [None, None])
        self.assertFalse(Translator("libretranslate", url="").enabled)
        self.assertFalse(Translator("off").enabled)

    def test_first_run_does_not_flood(self):
        app, sent = AppFlowTests().make_app(dt.date(2026, 11, 5))
        app.db.upsert_user(1, 1, "u", None, True)
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        async def fake_collect(db, sources):
            for i in range(6):
                db.add_news({"id": f"a{i}", "source": f"s{i}", "url": "u", "published_at": now,
                             "title": "Tragédia: ponte desaba em Minas Gerais e deixa mortos"})
            return 6

        import bot.app as app_mod
        orig = app_mod.news.collect
        app_mod.news.collect = fake_collect
        app.s.trends_url = ""
        try:
            asyncio.run(app.poll_news())
        finally:
            app_mod.news.collect = orig
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
