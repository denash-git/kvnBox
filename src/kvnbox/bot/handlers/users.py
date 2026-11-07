"""Пользователи: список, карточка, протоколы, ссылки и QR, добавление, удаление."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from kvnbox.bot.common import edit, notify_and_apply, run, show
from kvnbox.bot.keyboards import (
    back_to_main_keyboard,
    proto_delete_confirm_keyboard,
    proto_manage_keyboard,
    user_delete_confirm_keyboard,
    user_info_keyboard,
    users_list_keyboard,
)
from kvnbox.bot.legacy import TYPE_BY_TRANSPORT, parse_slot, proto_label, proto_type
from kvnbox.clients import awg as awg_client
from kvnbox.core.models import Connection, CoreKind, Protocol, User, supported_cores
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.users import UserService, core_suffix

router = Router()


class AddUserStates(StatesGroup):
    waiting_nickname = State()
    waiting_protocol = State()
    waiting_type = State()
    waiting_core = State()


def _find(app: App, nick: str, proto: str, utype: str) -> tuple[User, Connection] | None:
    user = app.users.get(nick)
    if user is None:
        return None
    conn = user.find(*parse_slot(proto, "" if utype == "none" else utype))
    return (user, conn) if conn else None


def _proto_text(nick: str, proto: str, utype: str, conn: Connection) -> str:
    return (
        f"⚙️ <b>Управление протоколом</b>\n\n"
        f"Пользователь: <code>{nick}</code>\n"
        f"Конфигурация: <code>{proto_label(proto, utype)}{core_suffix(conn)}</code>\n"
        f"Статус: {'🟢 Включен' if conn.enabled else '🔴 Выключен'}"
    )


async def _send_qr(cb: CallbackQuery, app: App, text: str, caption: str) -> None:
    if cb.bot is None:
        return
    with tempfile.TemporaryDirectory(dir=app.paths.root) as tmp:
        try:
            png = await run(UserService(app).qr_png, text, Path(tmp) / "qr.png")
            await cb.bot.send_photo(cb.from_user.id, FSInputFile(png), caption=caption)
        except ServiceError:
            pass


async def _send_awg(cb: CallbackQuery, app: App, user: User, conn: Connection) -> None:
    """AmneziaWG: файл .conf, его QR-код, ключ vpn:// для AmneziaVPN и его QR-код."""
    if cb.bot is None:
        return
    svc = UserService(app)
    conf = svc.awg_conf(user, conn)
    key = svc.awg_vpn_key(user, conn)
    chat = cb.from_user.id
    await cb.bot.send_document(
        chat,
        BufferedInputFile(conf.encode(), filename=awg_client.file_name(user)),
        caption=f"AmneziaWG 3.1 для {user.name}: файл конфигурации",
    )
    await _send_qr(
        cb, app, conf, f"QR-код AmneziaWG для {user.name} (приложения AmneziaWG и AmneziaVPN)"
    )
    await cb.bot.send_message(
        chat,
        f"🔐 <b>AmneziaVPN, ключ для {user.name}:</b>\n<code>{key}</code>",
        parse_mode="HTML",
    )
    await _send_qr(cb, app, key, f"QR-код ключа AmneziaVPN для {user.name}")


# ─── список и карточка ───────────────────────────────────────────────────────


@router.message(Command("users"))
@router.callback_query(F.data == "user:list")
async def users_list(event: Message | CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    await show(event, "👥 <b>Управление пользователями</b>", users_list_keyboard(app.users.all()))


@router.callback_query(F.data.startswith("user:info:"))
async def user_info(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    nick = (cb.data or "").split(":")[2]
    user = app.users.get(nick)
    if user is None:
        await cb.answer("Пользователь не найден.", show_alert=True)
        return
    text = (
        f"👤 <b>Пользователь:</b> <code>{nick}</code>\n"
        f"          {'🟢 Активен' if user.active else '🔴 Отключен'}\n\n"
        "<b>Добавленные протоколы:</b>"
    )
    await edit(cb, text, user_info_keyboard(user))
    await cb.answer()


# ─── протокол ────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("user:proto:manage:"))
async def proto_manage(cb: CallbackQuery, app: App) -> None:
    _, _, _, nick, proto, utype = (cb.data or "").split(":")
    found = _find(app, nick, proto, utype)
    if found is None:
        await cb.answer("Протокол не найден.", show_alert=True)
        return
    _, conn = found
    t = "" if utype == "none" else utype
    await edit(
        cb,
        _proto_text(nick, proto, t, conn),
        proto_manage_keyboard(nick, proto, utype, conn.enabled),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("user:proto:toggle:"))
async def proto_toggle(cb: CallbackQuery, app: App) -> None:
    _, _, _, nick, proto, utype, val = (cb.data or "").split(":")
    t = "" if utype == "none" else utype
    enabled = val == "1"
    svc = UserService(app)
    await notify_and_apply(
        cb,
        f"{'Включение' if enabled else 'Отключение'} протокола",
        lambda: svc.set_enabled(nick, enabled, *parse_slot(proto, t)),
    )
    found = _find(app, nick, proto, utype)
    if found:
        await edit(
            cb,
            _proto_text(nick, proto, t, found[1]),
            proto_manage_keyboard(nick, proto, utype, found[1].enabled),
        )


@router.callback_query(F.data.startswith("user:proto:delete:confirm:"))
async def proto_delete_confirm(cb: CallbackQuery) -> None:
    _, _, _, _, nick, proto, utype = (cb.data or "").split(":")
    label = proto_label(proto, "" if utype == "none" else utype)
    await edit(
        cb,
        f"⚠️ Вы уверены, что хотите удалить протокол <code>{label}</code> для "
        f"пользователя <code>{nick}</code>?",
        proto_delete_confirm_keyboard(nick, proto, utype),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("user:proto:delete:run:"))
async def proto_delete_run(cb: CallbackQuery, app: App) -> None:
    _, _, _, _, _, nick, proto, utype = (cb.data or "").split(":")
    t = "" if utype == "none" else utype
    svc = UserService(app)
    await notify_and_apply(
        cb, "Удаление протокола", lambda: svc.remove_connection(nick, *parse_slot(proto, t))
    )
    user = app.users.get(nick)
    if user is not None:
        text = (
            f"👤 <b>Пользователь:</b> <code>{nick}</code>\n"
            f"Общий статус: {'🟢 Активен' if user.active else '🔴 Отключен'}\n\n"
            "<b>Доступные протоколы (кликните для управления):</b>"
        )
        await edit(cb, text, user_info_keyboard(user))
    else:
        await edit(
            cb,
            "👥 <b>Список пользователей:</b>\nВыберите пользователя для управления "
            "или добавьте нового.",
            users_list_keyboard(app.users.all()),
        )


@router.callback_query(F.data.startswith("user:proto:links:"))
async def proto_links(cb: CallbackQuery, app: App) -> None:
    _, _, _, nick, proto, utype = (cb.data or "").split(":")
    await cb.answer("⏳ Генерация QR-кодов...", show_alert=False)
    found = _find(app, nick, proto, utype)
    if found is None or cb.bot is None:
        if isinstance(cb.message, Message):
            await cb.message.answer("❌ Ошибка: Протокол не найден.")
        return
    user, conn = found
    svc = UserService(app)
    if conn.protocol is Protocol.AWG:
        await _send_awg(cb, app, user, conn)
        return
    link = svc.link(user, conn)
    label = proto_label(proto, "" if utype == "none" else utype)
    await cb.bot.send_message(
        cb.from_user.id,
        f"🔑 <b>Подключение {nick} ({label}):</b>\n\n<code>{link}</code>",
        parse_mode="HTML",
    )
    await _send_qr(cb, app, link, f"QR-код подключения для {nick} ({label})")
    if conn.protocol is Protocol.MIERU:
        singbox_json = svc.mieru_configs(user, conn)["singbox"]
        await cb.bot.send_message(
            cb.from_user.id,
            f"📄 <b>Sing-box JSON для {nick} (копировать в Karing):</b>\n\n"
            f"<code>{singbox_json}</code>",
            parse_mode="HTML",
        )
        await _send_qr(cb, app, singbox_json, f"QR-код для Sing-box JSON ({nick})")


@router.callback_query(F.data.startswith("user:links:"))
async def user_links(cb: CallbackQuery, app: App) -> None:
    nick = (cb.data or "").split(":")[2]
    await cb.answer("⏳ Отправка ссылки подписки...", show_alert=False)
    user = app.users.get(nick)
    if user is None or cb.bot is None:
        return
    url = UserService(app).subscription_url(user)
    await cb.bot.send_message(
        cb.from_user.id,
        f"🌀 <b>Ссылка подписки для {nick}:</b>\n\n<code>{url}</code>",
        parse_mode="HTML",
    )
    await _send_qr(cb, app, url, f"QR-код подписки для {nick}")


# ─── удаление пользователя ───────────────────────────────────────────────────


@router.callback_query(F.data.startswith("user:delete:confirm:"))
async def delete_confirm(cb: CallbackQuery) -> None:
    nick = (cb.data or "").split(":")[3]
    await edit(
        cb,
        f"⚠️ Вы уверены, что хотите удалить пользователя <code>{nick}</code> и все его протоколы?",
        user_delete_confirm_keyboard(nick),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("user:delete:run:"))
async def delete_run(cb: CallbackQuery, app: App) -> None:
    nick = (cb.data or "").split(":")[3]
    svc = UserService(app)
    await notify_and_apply(cb, "Удаление пользователя", lambda: svc.delete(nick))
    await edit(cb, "👥 <b>Управление пользователями</b>", users_list_keyboard(app.users.all()))


# ─── добавление пользователя / протокола ─────────────────────────────────────

PROTO_BUTTONS = {
    "vless": Btn(text="VLESS", callback_data="user:new:proto:vless"),
    "naive": Btn(text="NaiveProxy", callback_data="user:new:proto:naive"),
    "mieru": Btn(text="Mieru", callback_data="user:new:proto:mieru"),
    "awg": Btn(text="AmneziaWG 3.1", callback_data="user:new:proto:awg"),
}


def _single_protos(app: App) -> tuple[str, ...]:
    """Протоколы без выбора транспорта; AmneziaWG — только когда включён (Модули в TUI)."""
    s = app.settings.load().awg
    return ("naive", "mieru", "awg") if s.enabled and s.configured else ("naive", "mieru")


VLESS_TYPES = [("ws", "WebSocket (ws)"), ("grpc", "gRPC"), ("xhttp", "HTTPUpgrade")]
# этап 11: настоящий XHTTP — callback «xh» («xhttp» исторически означает HTTPUpgrade)
XHTTP_TYPE = ("xh", "XHTTP (только Xray)")
CDN_TYPE = ("cdn", "CDN (через CDN, только Xray)")
CORE_SUFFIX = {"singbox": " · sing-box", "xray": " · Xray"}


def _vless_types(app: App) -> list[tuple[str, str]]:
    svc = UserService(app)
    types = VLESS_TYPES + ([XHTTP_TYPE] if svc.xray_ready() else [])
    return types + ([CDN_TYPE] if svc.cdn_ready() else [])


def _existing_types(user: User | None) -> set[str]:
    if user is None:
        return set()
    return {
        TYPE_BY_TRANSPORT[c.transport]
        for c in user.connections
        if c.protocol is Protocol.VLESS and c.transport
    }


def _cancel(app: App, nick: str) -> str:
    return f"user:info:{nick}" if app.users.get(nick) else "user:list"


@router.callback_query(F.data.startswith("user:proto:add:"))
async def proto_add(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    nick = (cb.data or "").split(":")[3]
    await state.set_state(AddUserStates.waiting_protocol)
    await state.update_data(nickname=nick)
    user = app.users.get(nick)
    have = {c.protocol.value for c in user.connections} if user else set()
    rows = []
    if len(_existing_types(user)) < len(_vless_types(app)):
        rows.append([PROTO_BUTTONS["vless"]])
    rows += [[PROTO_BUTTONS[p]] for p in _single_protos(app) if p not in have]
    rows.append([Btn(text="❌ Отмена", callback_data=f"user:info:{nick}")])
    await edit(
        cb,
        f"👤 Пользователь: <code>{nick}</code>\n\nВыберите новый протокол:",
        Kb(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data == "user:new:step1")
async def new_step1(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddUserStates.waiting_nickname)
    await edit(
        cb,
        "➕ <b>Добавление нового пользователя</b>\n\nВведите никнейм пользователя "
        "(только английские буквы, цифры, дефис и подчеркивание):",
        Kb(inline_keyboard=[[Btn(text="❌ Отмена", callback_data="user:list")]]),
    )
    await cb.answer()


@router.message(AddUserStates.waiting_nickname)
async def new_nickname(message: Message, app: App, state: FSMContext) -> None:
    nick = (message.text or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", nick):
        await message.answer(
            "❌ Неверный формат никнейма! Допускаются только буквы, цифры, "
            "дефис и подчеркивание. Введите еще раз:"
        )
        return
    await state.update_data(nickname=nick)
    await state.set_state(AddUserStates.waiting_protocol)
    kb = Kb(
        inline_keyboard=[
            [PROTO_BUTTONS["vless"]],
            *[[PROTO_BUTTONS[p]] for p in _single_protos(app)],
            [Btn(text="❌ Отмена", callback_data="user:list")],
        ]
    )
    await message.answer(
        f"Никнейм: <code>{nick}</code>\n\nВыберите протокол:", reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("user:new:proto:"))
async def new_proto(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    proto = (cb.data or "").split(":")[3]
    if proto not in PROTO_BUTTONS or (proto == "awg" and "awg" not in _single_protos(app)):
        await cb.answer()
        return
    await state.update_data(protocol=proto)
    nick = (await state.get_data()).get("nickname", "")
    if proto == "vless":
        await state.set_state(AddUserStates.waiting_type)
        have = _existing_types(app.users.get(nick))
        rows = [
            [Btn(text=label, callback_data=f"user:new:type:{t}")]
            for t, label in _vless_types(app)
            if t not in have
        ]
        rows.append([Btn(text="❌ Отмена", callback_data=_cancel(app, nick))])
        await edit(
            cb,
            f"Никнейм: <code>{nick}</code>\nПротокол: <code>VLESS</code>\n\n"
            "Выберите тип транспорта:",
            Kb(inline_keyboard=rows),
        )
    else:
        await state.update_data(type="")
        await _confirm(cb, app, nick, proto, "")
    await cb.answer()


@router.callback_query(F.data.startswith("user:new:type:"))
async def new_type(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    utype = (cb.data or "").split(":")[3]
    await state.update_data(type=utype)
    nick = (await state.get_data()).get("nickname", "")
    cores = supported_cores(*parse_slot("vless", utype))
    if len(cores) > 1 and UserService(app).xray_ready():
        # шаг «ядро» (этап 11) — только когда выбор есть; ядро потом не меняется
        await state.set_state(AddUserStates.waiting_core)
        await edit(
            cb,
            f"Никнейм: <code>{nick}</code>\nПротокол: <code>{proto_label('vless', utype)}</code>"
            "\n\nВыберите ядро:",
            Kb(
                inline_keyboard=[
                    [Btn(text="sing-box", callback_data="user:new:core:singbox")],
                    [Btn(text="Xray", callback_data="user:new:core:xray")],
                    [Btn(text="❌ Отмена", callback_data=_cancel(app, nick))],
                ]
            ),
        )
    else:
        core = cores[0].value if cores else ""
        await state.update_data(core=core)
        await _confirm(cb, app, nick, "vless", utype, core)
    await cb.answer()


@router.callback_query(F.data.startswith("user:new:core:"))
async def new_core(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    core = (cb.data or "").split(":")[3]
    if core not in CORE_SUFFIX:
        await cb.answer()
        return
    await state.update_data(core=core)
    data = await state.get_data()
    await _confirm(cb, app, data.get("nickname", ""), "vless", data.get("type", ""), core)
    await cb.answer()


async def _confirm(
    cb: CallbackQuery, app: App, nick: str, proto: str, utype: str, core: str = ""
) -> None:
    await edit(
        cb,
        (
            "⚙️ <b>Подтверждение создания/добавления:</b>\n\n"
            f"Никнейм: <code>{nick}</code>\n"
            f"Протокол: <code>{proto_label(proto, utype)}{CORE_SUFFIX.get(core, '')}</code>\n\n"
            "Выполнить операцию?"
        ),
        Kb(
            inline_keyboard=[
                [
                    Btn(text="✅ Подтвердить", callback_data="user:new:confirm:yes"),
                    Btn(text="❌ Отмена", callback_data=_cancel(app, nick)),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "user:new:confirm:yes")
async def new_confirm(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    nick, proto = data.get("nickname", ""), data.get("protocol", "")
    utype = data.get("type", "") or ("ws" if proto == "vless" else "")
    core = CoreKind(data["core"]) if data.get("core") in CORE_SUFFIX else None
    if not nick or proto not in PROTO_BUTTONS:
        await cb.answer()
        return
    await edit(cb, f"⏳ Добавление конфигурации для <code>{nick}</code>...")
    try:
        _, res = await run(UserService(app).add_connection, nick, *parse_slot(proto, utype), core)
        ok, msg = res.ok, res.message
    except ServiceError as e:  # в TransferBox ошибка добавления показывалась как успех
        ok, msg = False, str(e)
    if ok:
        await edit(
            cb,
            f"🎉 Конфигурация {proto_label(proto, utype)} для пользователя <code>{nick}</code> "
            "успешно создана!\nСлужбы перезапущены.",
            Kb(
                inline_keyboard=[
                    [
                        Btn(
                            text="« Перейти к управлению пользователем",
                            callback_data=f"user:info:{nick}",
                        )
                    ],
                    [Btn(text="« К списку пользователей", callback_data="user:list")],
                ]
            ),
        )
    else:
        await edit(
            cb, f"❌ Не удалось добавить конфигурацию:\n<code>{msg}</code>", back_to_main_keyboard()
        )
    await cb.answer()


__all__ = ["proto_type", "router"]
