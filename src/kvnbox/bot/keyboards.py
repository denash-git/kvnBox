"""Клавиатуры бота — порт bot/keyboards.py TransferBox один в один."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from kvnbox.bot.legacy import proto_label, proto_type
from kvnbox.core.models import SITE_TEMPLATE_LABELS, User
from kvnbox.services.users import core_suffix

MAIN = Btn(text="« Главное меню", callback_data="menu:main")
TO_SETTINGS = Btn(text="« Назад в Настройки", callback_data="settings:menu")


def align_button_text(text: str, target_width: float = 18.0) -> str:
    """Выравнивание текста кнопки по левому краю (оценка ширины пропорционального шрифта)."""
    width = 0.0
    for char in text:
        if char in "🟢🔴":
            width += 1.8
        elif char in "⚙️🚀👥📊🔄🔑🛡️🔓📋":
            width += 1.6
        elif char.isupper():
            width += 1.05
        elif char.islower():
            width += 0.8
        elif char == " ":
            width += 0.35
        else:
            width += 0.7
    spaces = int((target_width - width) / 0.35)
    if spaces > 0:
        # ​ — якорь: без него Telegram обрезает пробелы в конце кнопки
        return text + " " * spaces + "​"
    return text


def _a(text: str) -> str:
    return align_button_text(text, 18.0)


def main_menu_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text=_a("👥 Пользователи"), callback_data="user:list")],
            [Btn(text=_a("📊 Диагностика"), callback_data="diag:menu")],
            [Btn(text=_a("⚙️ Настройки"), callback_data="settings:menu")],
        ]
    )


def settings_menu_keyboard() -> Kb:
    """Управление BBR — только в меню на сервере (TUI); на его месте — перезагрузка."""
    return Kb(
        inline_keyboard=[
            [Btn(text=_a("🔐 Управление SSH"), callback_data="settings:ssh_menu")],
            [Btn(text=_a("🔄 Перезапуск служб"), callback_data="services:menu")],
            [Btn(text=_a("🔁 Перезагрузка сервера"), callback_data="settings:reboot")],
            [Btn(text=_a("🛡️ Защита от сканеров"), callback_data="settings:security_menu")],
            [Btn(text=_a("💾 Резервные копии"), callback_data="settings:backup_menu")],
            [Btn(text=_a("🌐 Шаблон сайта"), callback_data="settings:site_menu")],
            [Btn(text=_a("🔄 Проверить обновления"), callback_data="settings:check_updates")],
            [MAIN],
        ]
    )


def reboot_confirm_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text="✅ Да, перезагрузить", callback_data="settings:reboot_confirm")],
            [TO_SETTINGS],
            [MAIN],
        ]
    )


def site_template_keyboard(current: str) -> Kb:
    """Шаблоны фейкового сайта; текущий отмечен ✅."""
    rows = [
        [Btn(text=("✅ " if name == current else "") + label, callback_data=f"site:set:{name}")]
        for label, name in SITE_TEMPLATE_LABELS
    ]
    return Kb(inline_keyboard=[*rows, [TO_SETTINGS], [MAIN]])


def ssh_management_keyboard(mode: str = "password") -> Kb:
    if mode == "password":
        toggle, target = _a("🔒 Отключить вход по паролю"), "key"
    else:
        toggle, target = _a("🔑 Включить вход по паролю"), "password"
    return Kb(
        inline_keyboard=[
            [Btn(text=toggle, callback_data=f"ssh:toggle:{target}")],
            [Btn(text=_a("🔑 Изменить порт SSH"), callback_data="settings:ssh_port")],
            [TO_SETTINGS],
            [MAIN],
        ]
    )


def ssh_back_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text="« Назад к Управлению SSH", callback_data="settings:ssh_menu")],
            [MAIN],
        ]
    )


def security_menu_keyboard(active: bool, has_bans: bool = False) -> Kb:
    toggle = "🔴 Отключить защиту" if active else "🟢 Включить защиту"
    rows = [[Btn(text=_a(toggle), callback_data="settings:security_toggle")]]
    if active and has_bans:
        rows.append(
            [Btn(text=_a("🔓 Разблокировать всех"), callback_data="settings:security_unban_all")]
        )
    rows += [[TO_SETTINGS], [MAIN]]
    return Kb(inline_keyboard=rows)


def settings_backup_keyboard(enabled: bool) -> Kb:
    toggle = "🟢 Отключить автобэкапы" if enabled else "🔴 Включить автобэкапы"
    rows = [
        [Btn(text=_a("📥 Создать бэкап сейчас"), callback_data="settings:backup_now")],
        [Btn(text=_a(toggle), callback_data="settings:backup_toggle")],
    ]
    if enabled:
        rows.append(
            [Btn(text=_a("⏰ Время автобэкапа"), callback_data="settings:backup_change_time")]
        )
    rows += [[TO_SETTINGS], [MAIN]]
    return Kb(inline_keyboard=rows)


def settings_back_keyboard() -> Kb:
    return Kb(inline_keyboard=[[TO_SETTINGS], [MAIN]])


def settings_update_keyboard(has_update: bool) -> Kb:
    rows = []
    if has_update:
        rows.append([Btn(text="🔄 Обновить сейчас", callback_data="settings:run_update")])
    rows += [[TO_SETTINGS], [MAIN]]
    return Kb(inline_keyboard=rows)


def back_to_main_keyboard() -> Kb:
    return Kb(inline_keyboard=[[MAIN]])


def diagnostics_menu_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text=_a("📈 Мониторинг ресурсов"), callback_data="diag:resources")],
            [Btn(text=_a("🚀 Тест скорости"), callback_data="diag:speedtest")],
            [Btn(text=_a("🛰 Тест CDN"), callback_data="diag:cdn")],
            [Btn(text=_a("⚙️ Пересоздать конфиги"), callback_data="diag:recreate")],
            [MAIN],
        ]
    )


def diagnostics_back_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text="« Назад в Диагностику", callback_data="diag:menu")],
            [MAIN],
        ]
    )


def speedtest_menu_keyboard() -> Kb:
    # В TransferBox кнопка отправляла «diag:speed_run», на который не было обработчика —
    # тест не запускался. Обработчик kvnBox принимает оба значения.
    return Kb(
        inline_keyboard=[
            [Btn(text=_a("🌐 VPS → Интернет"), callback_data="diag:speed_ookla")],
            [Btn(text=_a("📶 Client ⇄ VPS"), callback_data="diag:speed_iperf")],
            [Btn(text="« Назад в Диагностику", callback_data="diag:menu")],
            [MAIN],
        ]
    )


def iperf_running_keyboard() -> Kb:
    return Kb(
        inline_keyboard=[
            [Btn(text=_a("🛑 Остановить iperf3"), callback_data="diag:iperf_stop")],
            [Btn(text="« Назад к тестам скорости", callback_data="diag:speedtest")],
            [MAIN],
        ]
    )


def services_menu_keyboard(xray: bool = False) -> Kb:
    """`xray` — кнопка Xray, когда служба должна работать (включён, есть подключения)."""
    return Kb(
        inline_keyboard=[
            [
                Btn(text="Caddy", callback_data="services:confirm:caddy"),
                Btn(text="sing-box", callback_data="services:confirm:sing-box"),
            ],
            *([[Btn(text="Xray", callback_data="services:confirm:xray")]] if xray else []),
            [
                Btn(text="Mieru", callback_data="services:confirm:mita"),
                Btn(text="NetBird", callback_data="services:confirm:netbird"),
            ],
            [Btn(text="🔄 Все службы", callback_data="services:confirm:all")],
            [TO_SETTINGS],
            [MAIN],
        ]
    )


def service_confirm_keyboard(service: str) -> Kb:
    return Kb(
        inline_keyboard=[
            [
                Btn(text="✅ Да, перезапустить", callback_data=f"services:run:{service}"),
                Btn(text="❌ Нет, отмена", callback_data="services:menu"),
            ]
        ]
    )


def users_list_keyboard(users: Sequence[User]) -> Kb:
    rows: list[list[Btn]] = []
    row: list[Btn] = []
    for name in sorted({u.name for u in users}):
        row.append(Btn(text=f"👤 {name}", callback_data=f"user:info:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn(text="➕ Добавить пользователя", callback_data="user:new:step1")])
    rows.append([MAIN])
    return Kb(inline_keyboard=rows)


def user_info_keyboard(user: User) -> Kb:
    rows: list[list[Btn]] = []
    for conn in user.connections:
        proto, utype = proto_type(conn)
        status = "🟢" if conn.enabled else "🔴"
        label = proto_label(proto, utype) + core_suffix(conn)
        rows.append(
            [
                Btn(
                    text=align_button_text(f"{status} {label}", 18.0),
                    callback_data=f"user:proto:manage:{user.name}:{proto}:{utype or 'none'}",
                )
            ]
        )
    buttons = []
    if len({c.slot for c in user.connections}) < 7:  # 7 слотов с XHTTP и CDN (этап 11)
        buttons.append(Btn(text="➕ Протокол", callback_data=f"user:proto:add:{user.name}"))
    buttons.append(Btn(text="🌀 Подписка", callback_data=f"user:links:{user.name}"))
    rows.append(buttons)
    rows.append(
        [Btn(text="🗑️ Удалить пользователя", callback_data=f"user:delete:confirm:{user.name}")]
    )
    rows.append([Btn(text="« К списку пользователей", callback_data="user:list")])
    return Kb(inline_keyboard=rows)


def proto_manage_keyboard(nick: str, proto: str, utype: str, enabled: bool) -> Kb:
    toggle = "🔴 Отключить протокол" if enabled else "🟢 Включить протокол"
    t = utype if utype and utype != "none" else "none"
    return Kb(
        inline_keyboard=[
            [
                Btn(
                    text=toggle,
                    callback_data=f"user:proto:toggle:{nick}:{proto}:{t}:{'0' if enabled else '1'}",
                )
            ],
            [
                Btn(
                    text="🔑 Получить ссылку & QR",
                    callback_data=f"user:proto:links:{nick}:{proto}:{t}",
                )
            ],
            [
                Btn(
                    text="🗑️ Удалить этот протокол",
                    callback_data=f"user:proto:delete:confirm:{nick}:{proto}:{t}",
                )
            ],
            [Btn(text="« Назад к пользователю", callback_data=f"user:info:{nick}")],
            [MAIN],
        ]
    )


def proto_delete_confirm_keyboard(nick: str, proto: str, utype: str) -> Kb:
    t = utype if utype and utype != "none" else "none"
    return Kb(
        inline_keyboard=[
            [
                Btn(
                    text="🗑️ Да, удалить", callback_data=f"user:proto:delete:run:{nick}:{proto}:{t}"
                ),
                Btn(text="❌ Нет, отмена", callback_data=f"user:proto:manage:{nick}:{proto}:{t}"),
            ],
            [MAIN],
        ]
    )


def user_delete_confirm_keyboard(nick: str) -> Kb:
    return Kb(
        inline_keyboard=[
            [
                Btn(text="🗑️ Да, удалить", callback_data=f"user:delete:run:{nick}"),
                Btn(text="❌ Нет, отмена", callback_data=f"user:info:{nick}"),
            ],
            [MAIN],
        ]
    )
