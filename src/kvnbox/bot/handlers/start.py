"""Главное меню."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from kvnbox import __version__
from kvnbox.bot.common import show
from kvnbox.bot.keyboards import main_menu_keyboard

router = Router()


def start_text() -> str:
    return f"📦 <b>kvnBox v. {__version__}</b>\n<b>Панель Управления</b>"


@router.message(Command("start"))
@router.callback_query(F.data == "menu:main")
async def main_menu(event: Message | CallbackQuery) -> None:
    await show(event, start_text(), main_menu_keyboard())
