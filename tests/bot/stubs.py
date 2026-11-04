"""Заглушки aiogram для прогона обработчиков без Telegram.

Наследники настоящих CallbackQuery/Message (проверки isinstance в обработчиках
работают), все ответы записываются в `REC`. Модуль самодостаточен: этим же
файлом на стенде прогоняются и обработчики старого бота TransferBox.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

CHAT_ID = 100000001
REC: list[dict[str, Any]] = []


def _kb(markup: Any) -> list[list[list[str]]] | None:
    if markup is None:
        return None
    return [[[b.text, b.callback_data or ""] for b in row] for row in markup.inline_keyboard]


class FakeBot:
    id = 1

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> None:
        REC.append({"op": "send_message", "text": text, "kb": _kb(kw.get("reply_markup"))})

    async def send_photo(self, chat_id: int, photo: Any, caption: str = "", **kw: Any) -> None:
        REC.append({"op": "send_photo", "caption": caption})


BOT = FakeBot()


class FakeMessage(Message):
    async def edit_text(self, text: str, reply_markup: Any = None, **kw: Any) -> None:  # type: ignore[override]
        REC.append({"op": "edit_text", "text": text, "kb": _kb(reply_markup)})

    async def answer(self, text: str, reply_markup: Any = None, **kw: Any) -> None:  # type: ignore[override]
        REC.append({"op": "answer", "text": text, "kb": _kb(reply_markup)})

    async def delete(self, **kw: Any) -> None:  # type: ignore[override]
        REC.append({"op": "delete"})

    @property
    def bot(self) -> Any:  # type: ignore[override]
        return BOT


class FakeCallback(CallbackQuery):
    async def answer(
        self,
        text: str | None = None,
        show_alert: bool | None = None,  # type: ignore[override]
        **kw: Any,
    ) -> None:
        if text:
            REC.append({"op": "alert", "text": text, "show_alert": bool(show_alert)})

    @property
    def bot(self) -> Any:  # type: ignore[override]
        return BOT


def _user() -> User:
    return User.model_construct(id=CHAT_ID, is_bot=False, first_name="Admin")


def message(text: str = "") -> FakeMessage:
    return FakeMessage.model_construct(
        message_id=1,
        date=dt.datetime.now(),
        chat=Chat.model_construct(id=CHAT_ID, type="private"),
        from_user=_user(),
        text=text,
    )


def callback(data: str) -> FakeCallback:
    return FakeCallback.model_construct(
        id="1",
        from_user=_user(),
        chat_instance="x",
        data=data,
        message=message(),
    )


def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID)
    )
