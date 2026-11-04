"""Запуск бота: `kvnbox bot run` (служба kvnbox-bot)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import BotCommand, TelegramObject, Update

from kvnbox.bot.handlers import routers
from kvnbox.services.app import App


class AuthMiddleware(BaseMiddleware):
    """Обрабатываются только апдейты от владельца (TG_CHAT_ID). Настройки
    перечитываются на каждый апдейт — смена Chat ID в меню действует сразу."""

    def __init__(self, app: App) -> None:
        self.app = app

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        allowed = self.app.settings.load().telegram.chat_id
        user = None
        if isinstance(event, Update):
            for part in (event.message, event.callback_query, event.edited_message):
                if part is not None and part.from_user is not None:
                    user = part.from_user
                    break
        if user is not None and allowed and str(user.id) == str(allowed):
            return await handler(event, data)
        return None


def build_dispatcher(app: App) -> Dispatcher:
    dp = Dispatcher()
    dp["app"] = app  # внедряется в обработчики параметром `app`
    dp.update.outer_middleware(AuthMiddleware(app))
    for r in routers():
        dp.include_router(r)
    return dp


async def _main(app: App) -> int:
    tg = app.settings.load().telegram
    if not tg.enabled:
        print("[kvnBox bot] Бот выключен в настройках.")
        return 0
    if not tg.token:
        print("[kvnBox bot] Не задан токен бота.", file=sys.stderr)
        return 1
    bot = Bot(token=tg.token)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню управления")])
    dp = build_dispatcher(app)
    print("[kvnBox bot] Запуск polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
    return 0


def run(app: App) -> int:
    return asyncio.run(_main(app))
