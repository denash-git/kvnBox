"""Перезапуск служб."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from kvnbox.bot.common import edit, run, show
from kvnbox.bot.keyboards import (
    back_to_main_keyboard,
    service_confirm_keyboard,
    services_menu_keyboard,
)
from kvnbox.cores.xray import xray_running
from kvnbox.services.app import App

router = Router()
ALLOWED = {"caddy", "sing-box", "xray", "mita", "netbird", "kvnbox-awg", "all"}


@router.message(Command("services"))
@router.callback_query(F.data == "services:menu")
async def services_menu(event: Message | CallbackQuery, app: App) -> None:
    await show(
        event,
        "🔄 <b>Управление службами</b>\n\nВыберите службу для перезапуска:",
        services_menu_keyboard(_xray_needed(app), _awg_on(app)),
    )


def _xray_needed(app: App) -> bool:
    return xray_running(app.settings.load(), app.users.all())


def _awg_on(app: App) -> bool:
    s = app.settings.load().awg
    return s.enabled and s.configured


@router.callback_query(F.data.startswith("services:confirm:"))
async def services_confirm(cb: CallbackQuery) -> None:
    service = (cb.data or "").split(":")[2]
    label = "все прокси-службы" if service == "all" else f"службу {service}"
    await edit(
        cb, f"❓ Вы действительно хотите перезапустить {label}?", service_confirm_keyboard(service)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("services:run:"))
async def services_run(cb: CallbackQuery, app: App) -> None:
    service = (cb.data or "").split(":")[2]
    if service not in ALLOWED:
        await cb.answer()
        return
    label = "Все службы" if service == "all" else service
    await edit(cb, f"⏳ Перезапуск {label}...")
    if service == "all":
        units = ["caddy", "sing-box", *(["xray"] if _xray_needed(app) else [])]
        units += [
            u for u, binary in (("mita", "mita"), ("netbird", "netbird")) if app.installed(binary)
        ]
        units += ["kvnbox-awg"] if _awg_on(app) else []
    elif service == "xray" and not _xray_needed(app):
        await edit(cb, "⚪ Xray не нужен: отключён или нет подключений.", back_to_main_keyboard())
        await cb.answer()
        return
    else:
        units = [service]
    failed = [u for u in units if not (await run(app.systemd.restart, u)).ok]
    if not failed:
        text = f"🟢 <b>Успешно!</b>\nПерезапуск {label} выполнен успешно."
    else:
        text = f"🔴 <b>Ошибка!</b>\nНе удалось перезапустить службы: {', '.join(failed)}."
    await edit(cb, text, back_to_main_keyboard())
    await cb.answer()
