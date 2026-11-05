"""Команды и кнопки бота (aiogram 3)."""
from __future__ import annotations

import datetime as dt
import html
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import cards
from .app import App
from .db import USER_CATEGORIES
from .states import ALL_UF, STATES, normalize_scope, state_name

router = Router()
APP: App | None = None  # подставляется в main.py

CATEGORY_LABELS = {
    "breaking": "⚡ Срочные новости",
    "digest": "📰 Утренний дайджест",
    "holiday": "🇧🇷 Праздники (страна и штаты)",
    "festival": "🎉 Фестивали и шоу",
    "sport": "⚽ Спорт: финалы, турниры",
    "commercial": "🛍 Коммерческие даты",
}

HELP_TEXT = """<b>СМИ Бот · Бразилия</b>

Что приходит автоматически:
⚡ громкие новости — сразу, круглосуточно
📰 дайджест главного за сутки — каждое утро
🗓 праздники, фестивали, матчи — {days}
📅 обзор недели — по понедельникам

Команды:
/week — события на 7 дней
/month — события на 30 дней
/news — главное за сутки
/states — выбрать штаты
/settings — что присылать
/help — эта справка"""

ADMIN_HELP = """<b>Команды администратора</b>
/add 21.11.2026 | Название | SP,RJ или BR | категория | описание
   (категории: holiday, festival, sport, commercial, culture; диапазон: 05.02.2027-10.02.2027)
/del 12 — убрать событие, добавленное вручную или из новостей
/custom — список добавленных событий
/stats — статистика
/sources — состояние RSS-лент
/reload — перечитать events.yaml и sources.yaml
/test — прислать себе напоминания, дайджест и срочную новость прямо сейчас
/fetch — собрать новости сейчас
/broadcast текст — сообщение всем подписчикам"""


def _app() -> App:
    assert APP is not None
    return APP


def _is_admin(user_id: int) -> bool:
    return user_id in _app().s.admin_ids


def _authorized(user_id: int) -> bool:
    app = _app()
    if not app.s.access_code or _is_admin(user_id):
        return True
    u = app.db.get_user(user_id)
    return bool(u and u["authorized"])


def _remind_days_text() -> str:
    from .formatting import remind_schedule

    return remind_schedule(_app().s.remind_days)


# ───────────── клавиатуры ─────────────

def states_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    all_on = "ALL" in selected
    rows = [[InlineKeyboardButton(text=("✅ " if all_on else "") + "🇧🇷 Все штаты", callback_data="st:ALL")]]
    row: list[InlineKeyboardButton] = []
    for uf in ALL_UF:
        mark = "✅" if (all_on or uf in selected) else ""
        row.append(InlineKeyboardButton(text=f"{mark}{uf}", callback_data=f"st:{uf}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="Только общенациональное", callback_data="st:NONE"),
        InlineKeyboardButton(text="Готово ✔️", callback_data="st:DONE"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def states_text(selected: list[str]) -> str:
    if "ALL" in selected:
        chosen = "все штаты"
    elif selected:
        chosen = ", ".join(f"{state_name(uf)} ({uf})" for uf in selected)
    else:
        chosen = "пока ни одного — будут только общенациональные события"
    legend = "  ".join(f"{uf} — {STATES[uf][0]}" for uf in ALL_UF)
    return (
        "📍 <b>Выберите штаты</b>\n"
        "Общенациональные новости и праздники приходят всем. Штаты нужны для региональных праздников и новостей.\n\n"
        f"<b>Выбрано:</b> {chosen}\n\n<i>{legend}</i>"
    )


def settings_keyboard(cats: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=("✅ " if c in cats else "▫️ ") + CATEGORY_LABELS[c], callback_data=f"cat:{c}")]
        for c in USER_CATEGORIES
    ]
    rows.append([InlineKeyboardButton(text="📍 Изменить штаты", callback_data="open:states")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────── доступ и старт ─────────────

async def _ensure_user(message: Message) -> dict:
    app = _app()
    u = message.from_user
    name = " ".join(x for x in [u.first_name, u.last_name] if x)
    return app.db.upsert_user(u.id, message.chat.id, name, u.username, authorized=_authorized(u.id))


async def _welcome(message: Message, user: dict) -> None:
    app = _app()
    png = cards.welcome_card()
    await message.answer_photo(
        BufferedInputFile(png, "welcome.png"),
        caption=HELP_TEXT.format(days=_remind_days_text()),
    )
    await message.answer(states_text(user["states"]), reply_markup=states_keyboard(user["states"]))
    if not app.s.ai_enabled and not app.translator.enabled:
        await message.answer("ℹ️ Переводчик пока не подключён — заголовки новостей придут на португальском.")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    app = _app()
    user = await _ensure_user(message)
    if not user["authorized"]:
        code = (command.args or "").strip()
        if code and code == app.s.access_code:
            app.db.set_authorized(user["user_id"])
            user = app.db.get_user(user["user_id"])
        else:
            await message.answer("🔒 Это закрытый бот для команды. Отправьте код доступа одним сообщением.")
            return
    await _welcome(message, user)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = HELP_TEXT.format(days=_remind_days_text())
    if _is_admin(message.from_user.id):
        text += "\n\n" + ADMIN_HELP
    await message.answer(text)


@router.message(Command("states"))
async def cmd_states(message: Message) -> None:
    user = await _ensure_user(message)
    if not user["authorized"]:
        return
    await message.answer(states_text(user["states"]), reply_markup=states_keyboard(user["states"]))


@router.callback_query(F.data.startswith("st:"))
async def cb_states(call: CallbackQuery) -> None:
    app = _app()
    user = app.db.get_user(call.from_user.id)
    if not user or not user["authorized"]:
        await call.answer()
        return
    val = call.data.split(":", 1)[1]
    sel = list(user["states"])
    if val == "DONE":
        await call.message.edit_text(states_text(sel))
        await call.answer("Сохранено")
        await call.message.answer("Готово! Посмотреть ближайшие события: /week · /month. Что присылать: /settings")
        return
    if val == "ALL":
        sel = [] if "ALL" in sel else ["ALL"]
    elif val == "NONE":
        sel = []
    elif val in STATES:
        if "ALL" in sel:
            sel = [uf for uf in ALL_UF if uf != val]
        elif val in sel:
            sel.remove(val)
        else:
            sel.append(val)
        if len(sel) == len(ALL_UF):
            sel = ["ALL"]
    app.db.set_states(user["user_id"], sel)
    try:
        await call.message.edit_text(states_text(sel), reply_markup=states_keyboard(sel))
    except Exception:  # noqa: BLE001 — «message is not modified»
        pass
    await call.answer()


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user = await _ensure_user(message)
    if not user["authorized"]:
        return
    await message.answer("⚙️ <b>Что присылать</b>\nНажмите, чтобы включить или выключить:", reply_markup=settings_keyboard(user["categories"]))


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery) -> None:
    app = _app()
    user = app.db.get_user(call.from_user.id)
    if not user or not user["authorized"]:
        await call.answer()
        return
    c = call.data.split(":", 1)[1]
    cats = list(user["categories"])
    if c in cats:
        cats.remove(c)
    elif c in USER_CATEGORIES:
        cats.append(c)
    app.db.set_categories(user["user_id"], cats)
    await call.message.edit_reply_markup(reply_markup=settings_keyboard(cats))
    await call.answer("Сохранено")


@router.callback_query(F.data == "open:states")
async def cb_open_states(call: CallbackQuery) -> None:
    user = _app().db.get_user(call.from_user.id)
    if user:
        await call.message.answer(states_text(user["states"]), reply_markup=states_keyboard(user["states"]))
    await call.answer()


# ───────────── просмотр ─────────────

@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    user = await _ensure_user(message)
    if user["authorized"]:
        await _app().send_agenda(user, 7, "Ближайшие 7 дней")


@router.message(Command("month"))
async def cmd_month(message: Message) -> None:
    user = await _ensure_user(message)
    if user["authorized"]:
        await _app().send_agenda(user, 30, "Ближайшие 30 дней")


@router.message(Command("news"))
async def cmd_news(message: Message) -> None:
    app = _app()
    user = await _ensure_user(message)
    if not user["authorized"]:
        return
    from .app import _news_for_user

    items = [i for i in app.db.digest_candidates(24, app.s.digest_min_importance) if _news_for_user(i, user)]
    if not items:
        await message.answer("Пока нет важных новостей за последние сутки. Новости собираются каждые "
                             f"{app.s.news_interval_min} минут.")
        return
    await app.send_digest(user, items[: app.s.digest_size], app.today())


# ───────────── администратор ─────────────

_DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"


def _parse_date(s: str) -> dt.date:
    m = re.fullmatch(_DATE, s.strip())
    if not m:
        raise ValueError(f"Не понял дату «{s}» — нужен формат ДД.ММ.ГГГГ")
    d, mth, y = map(int, m.groups())
    return dt.date(y, mth, d)


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    app = _app()
    if not _is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (command.args or "").split("|")]
    if len(parts) < 2:
        await message.answer(ADMIN_HELP)
        return
    try:
        if "-" in parts[0]:
            a, b = parts[0].split("-", 1)
            start, end = _parse_date(a), _parse_date(b)
        else:
            start = end = _parse_date(parts[0])
        if end < start:
            raise ValueError("Дата окончания раньше начала")
    except ValueError as e:
        await message.answer(f"⚠️ {html.escape(str(e))}")
        return
    cat = parts[3].lower() if len(parts) > 3 and parts[3] else "festival"
    if cat not in {"holiday", "festival", "sport", "commercial", "culture", "state_holiday"}:
        cat = "festival"
    ev = {
        "name_ru": parts[1],
        "scope": normalize_scope(parts[2] if len(parts) > 2 else "BR"),
        "category": cat,
        "about": parts[4] if len(parts) > 4 else "",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "importance": 3,
        "source": "admin",
    }
    eid = app.db.add_custom_event(ev)
    await message.answer(f"✅ Добавлено (№{eid}): <b>{html.escape(ev['name_ru'])}</b>, {start:%d.%m.%Y}"
                         f"{'–' + end.strftime('%d.%m.%Y') if end != start else ''}. Удалить: /del {eid}")


@router.message(Command("del"))
async def cmd_del(message: Message, command: CommandObject) -> None:
    if not _is_admin(message.from_user.id):
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Укажите номер: /del 12 (номера — в /custom)")
        return
    ok = _app().db.deactivate_custom_event(int(arg))
    await message.answer("🗑 Удалено" if ok else "Не нашёл такого события")


@router.message(Command("custom"))
async def cmd_custom(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    evs = _app().db.custom_events(include_past_days=0)
    if not evs:
        await message.answer("Добавленных событий нет.")
        return
    lines = ["<b>Добавленные события</b> (вручную ✍️ / из новостей 🤖)"]
    for e in evs:
        icon = "🤖" if e.source == "news" else "✍️"
        lines.append(f"{icon} №{e.db_id} · {e.rule['date']} · {html.escape(e.name_ru)} · {', '.join(e.scope)}")
    await _app().send_text(message.chat.id, "\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    app = _app()
    if not _is_admin(message.from_user.id):
        return
    total, active = app.db.user_counts()
    ns = app.db.news_stats()
    tin = int(app.db.kv_get("ai_input_tokens", "0") or 0)
    tout = int(app.db.kv_get("ai_output_tokens", "0") or 0)
    await message.answer(
        "📊 <b>Статистика</b>\n"
        f"Пользователи: {active} активных из {total}\n"
        f"Новостей за сутки: {ns['fetched_24h']}, важных: {ns['important_24h']}, срочных отправлено: {ns['breaking_24h']}\n"
        f"Режим новостей: {'Claude (' + app.s.claude_model + ')' if app.s.ai_enabled else 'бесплатный — охват изданий + тренды Google'}\n"
        f"Перевод: {'Claude' if app.s.ai_enabled else app.translator.label}"
        f"{'' if app.s.ai_enabled or not app.s.trends_url else ' · трендов сейчас: ' + str(len(app.trends))}\n"
        f"Токены за всё время: {tin:,} вход / {tout:,} выход".replace(",", " ")
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    app = _app()
    if not _is_admin(message.from_user.id):
        return
    st = {s["name"]: s for s in app.db.feed_statuses()}
    lines = ["📡 <b>RSS-ленты</b>"]
    for src in app.sources:
        s = st.get(src["name"])
        if not s:
            lines.append(f"⏳ {html.escape(src['name'])} — ещё не опрашивалась")
        elif s["last_error"]:
            lines.append(f"❌ {html.escape(src['name'])} — {html.escape(s['last_error'][:120])}")
        else:
            lines.append(f"✅ {html.escape(src['name'])} — {s['last_count']} новостей в ленте")
    await message.answer("\n".join(lines))


@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        n_ev, n_src = _app().reload()
        await message.answer(f"🔄 Перечитано: {n_ev} событий календаря, {n_src} лент.")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"⚠️ Ошибка в файле: <code>{html.escape(str(e))}</code>\nСтарые данные оставлены.")


@router.message(Command("fetch"))
async def cmd_fetch(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer("⏳ Собираю новости…")
    await _app().poll_news()
    ns = _app().db.news_stats()
    await message.answer(f"Готово. За сутки: {ns['fetched_24h']} новостей, важных: {ns['important_24h']}.")


@router.message(Command("test"))
async def cmd_test(message: Message) -> None:
    app = _app()
    if not _is_admin(message.from_user.id):
        return
    user = await _ensure_user(message)
    n = await app.daily_reminders(only_user=user, dry_run=True)
    if not n:
        await message.answer("Напоминаний на сегодня по вашим настройкам нет — вот ближайшие события:")
        await app.send_agenda(user, 14, "Ближайшие 14 дней")
    items = app.db.digest_candidates(24, app.s.digest_min_importance)
    if items:
        await app.send_digest(user, items[: app.s.digest_size], app.today())
        top = items[0]
        from .formatting import news_caption
        from .app import tz_label
        from .news import download_image

        await app.ensure_translated([top])
        photo = await download_image(top.get("image_url"))
        png = cards.news_card(top, app.s.tz, True, photo, tz_label(app.s.timezone_name))
        await app.send_photo(message.chat.id, png, "🧪 Пример срочной новости:\n\n" + news_caption(top, app.s.tz, True, tz_label(app.s.timezone_name)))
    else:
        await message.answer("Новостей пока нет — запустите /fetch.")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    app = _app()
    if not _is_admin(message.from_user.id):
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("Напишите текст: /broadcast Завтра совещание по карнавалу")
        return
    n = 0
    for u in app.db.active_users():
        if await app.send_text(u["chat_id"], f"📢 {html.escape(text)}", u["user_id"]):
            n += 1
    await message.answer(f"Отправлено: {n}")


# ───────────── код доступа / прочее ─────────────

@router.message(F.text)
async def any_text(message: Message) -> None:
    app = _app()
    user = await _ensure_user(message)
    if not user["authorized"]:
        if app.s.access_code and message.text.strip() == app.s.access_code:
            app.db.set_authorized(user["user_id"])
            await _welcome(message, app.db.get_user(user["user_id"]))
        else:
            await message.answer("🔒 Неверный код. Попросите код у руководителя.")
        return
    await message.answer("Я присылаю новости и напоминания сам. Команды: /week · /month · /news · /settings · /help")
