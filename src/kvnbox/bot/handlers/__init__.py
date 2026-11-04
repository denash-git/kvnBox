"""Обработчики бота по разделам."""

from aiogram import Router

from kvnbox.bot.handlers import services, settings, start, status, users


def routers() -> list[Router]:
    return [start.router, users.router, status.router, services.router, settings.router]
