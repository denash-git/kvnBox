"""Общее для обработчиков: показ экрана, применение изменений с уведомлением."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from kvnbox.services import ServiceError
from kvnbox.services.apply import ApplyResult

T = TypeVar("T")


async def show(
    event: CallbackQuery | Message, text: str, kb: InlineKeyboardMarkup | None = None
) -> None:
    """Callback — отредактировать сообщение (если нельзя — прислать новое и удалить
    старое), команда — ответить сообщением. Как в TransferBox."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await msg.answer(text, reply_markup=kb, parse_mode="HTML")
                await msg.delete()
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


async def edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    if isinstance(cb.message, Message):
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


async def run(fn: Callable[..., T], *args: Any) -> T:
    """Синхронную операцию (команды, перезапуск служб) — в отдельном потоке."""
    return await asyncio.to_thread(fn, *args)


async def notify_and_apply(
    cb: CallbackQuery, action: str, change: Callable[[], ApplyResult]
) -> bool:
    """«⏳ Применение изменений…» → операция → всплывающее уведомление (как
    notify_and_restart в TransferBox). При неудаче изменения уже откатаны (D-028)."""
    await edit(cb, f"⏳ Применение изменений: {action}...")
    try:
        res = await run(change)
    except ServiceError as e:
        await cb.answer(f"❌ Ошибка: {e}"[:200], show_alert=True)
        return False
    if res.ok:
        await cb.answer("✓ Успешно выполнено!", show_alert=True)
        return True
    await cb.answer(f"❌ Ошибка: {res.message}"[:200], show_alert=True)
    return False
