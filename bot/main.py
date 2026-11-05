"""Точка входа.

Обычный сервер:            python -m bot.main
Короткий запуск (GitHub):  python -m bot.main --run-minutes 8
    бот работает N минут — отвечает на команды, собирает новости, отправляет
    срочные и всё, что пора по расписанию, — и аккуратно завершается.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import handlers
from .app import App
from .config import load_settings

log = logging.getLogger("smi_bot")


class LastUpdate(BaseMiddleware):
    """Запоминает номер последнего обработанного обновления Telegram."""

    def __init__(self) -> None:
        self.max_id = 0

    async def __call__(self, handler, event, data):
        self.max_id = max(self.max_id, getattr(event, "update_id", 0) or 0)
        return await handler(event, data)


async def main(run_minutes: float = 0) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    s = load_settings()
    if not s.bot_token:
        sys.exit("Не задан BOT_TOKEN — получите его у @BotFather")

    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    app = App(s, bot)
    handlers.APP = app

    dp = Dispatcher()
    dp.include_router(handlers.router)
    last = LastUpdate()
    dp.update.outer_middleware(last)

    await bot.set_my_commands([
        BotCommand(command="week", description="События на 7 дней"),
        BotCommand(command="month", description="События на 30 дней"),
        BotCommand(command="news", description="Главное за сутки"),
        BotCommand(command="states", description="Выбрать штаты"),
        BotCommand(command="settings", description="Что присылать"),
        BotCommand(command="help", description="Справка"),
    ])

    short_run = run_minutes > 0
    sched = AsyncIOScheduler(timezone=s.tz)
    now = dt.datetime.now(s.tz)
    news_every = min(s.news_interval_min, 5) if short_run else s.news_interval_min
    sched.add_job(app.poll_news, "interval", minutes=news_every, next_run_time=now + dt.timedelta(seconds=3),
                  id="news", max_instances=1, coalesce=True)
    sched.add_job(app.run_due_tasks, "interval", minutes=1, next_run_time=now + dt.timedelta(seconds=30),
                  id="daily", max_instances=1, coalesce=True)
    sched.start()

    log.info(
        "СМИ Бот запущен%s. Пояс %s, напоминания за %s дн. в %02d:%02d, дайджест в %02d:%02d, новости: %s",
        f" на {run_minutes:g} мин" if short_run else "", s.timezone_name, s.remind_days, *s.remind_time,
        *s.digest_time, "Claude" if s.ai_enabled else "бесплатный режим",
    )
    if not short_run:
        await app.notify_admins("✅ СМИ Бот запущен. Справка для администратора: /help")

    async def stop_later() -> None:
        await asyncio.sleep(run_minutes * 60)
        log.info("Время запуска вышло — завершаем")
        await dp.stop_polling()

    stopper = asyncio.create_task(stop_later()) if short_run else None
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=not short_run)
    finally:
        if stopper:
            stopper.cancel()
        sched.shutdown(wait=False)
        # даём доработать начатым рассылкам
        await asyncio.sleep(2)
        if last.max_id:
            try:  # подтверждаем Telegram обработанные обновления, чтобы не получить их снова при следующем запуске
                await bot.get_updates(offset=last.max_id + 1, limit=1, timeout=0)
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось подтвердить обновления: %s", e)
        await bot.session.close()
        app.db.close()


def cli() -> None:
    p = argparse.ArgumentParser(description="СМИ Бот")
    p.add_argument("--run-minutes", type=float, default=float(os.environ.get("RUN_MINUTES", "0") or 0),
                   help="проработать N минут и завершиться (для запуска по расписанию)")
    args = p.parse_args()
    asyncio.run(main(args.run_minutes))


if __name__ == "__main__":
    cli()
