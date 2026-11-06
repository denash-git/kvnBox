"""Настройки: SSH, BBR, обновления, бэкапы, шаблон сайта, защита от сканеров."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from kvnbox.bot.common import edit, notify_and_apply, run, show
from kvnbox.bot.keyboards import (
    security_menu_keyboard,
    settings_back_keyboard,
    settings_backup_keyboard,
    settings_menu_keyboard,
    settings_update_keyboard,
    site_template_keyboard,
    ssh_back_keyboard,
    ssh_management_keyboard,
)
from kvnbox.core.models import FAKE_SITE_TEMPLATES, SITE_TEMPLATE_LABELS
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.backup import BackupService
from kvnbox.services.jobs import Jobs
from kvnbox.services.modules import BbrModule
from kvnbox.services.security import SecurityService
from kvnbox.services.settings import SettingsService
from kvnbox.services.ssh import SshService, SshStatus
from kvnbox.services.update import UpdateService
from kvnbox.system.fail2ban import SecurityStatus

router = Router()


class SettingsStates(StatesGroup):
    waiting_ssh_port = State()
    waiting_backup_time = State()


SETTINGS_TEXT = "⚙️ <b>Настройки системы</b>"


@router.message(Command("settings"))
@router.callback_query(F.data == "settings:menu")
async def settings_menu(event: Message | CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    await show(event, SETTINGS_TEXT, settings_menu_keyboard(app.bbr.is_active()))


# ─── SSH ─────────────────────────────────────────────────────────────────────


def ssh_text(st: SshStatus) -> str:
    lines = [
        "🔐 <b>Управление SSH</b>\n",
        f"• Порт SSH: <code>{st.port}</code>",
        # вход по паролю — слабое место: включён — красным (как в меню)
        f"• Вход по паролю: {'🔴 Включен' if st.password_allowed else '🟢 Отключен'}",
        "• Вход по ключу: 🟢 Включен",
        f"• Установлено ключей: <code>{len(st.keys)}</code>\n",
    ]
    # сами ключи в Telegram не показываем — только количество
    lines.append("<i>При отключении входа по паролю доступ будет возможен только по SSH-ключу</i>")
    return "\n".join(lines)


@router.callback_query(F.data.in_({"settings:ssh_menu", "settings:ssh_auth"}))
async def ssh_menu(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    st = await run(SshService(app).status)
    await edit(cb, ssh_text(st), ssh_management_keyboard(st.auth_mode))
    await cb.answer()


@router.callback_query(F.data.startswith("ssh:toggle:"))
async def ssh_toggle(cb: CallbackQuery, app: App) -> None:
    target = (cb.data or "").split(":")[2]
    if target not in ("key", "password"):
        await cb.answer()
        return
    svc = SshService(app)
    if target == "key" and not (await run(svc.status)).keys:
        await cb.answer("Сначала сгенерируйте ключ в меню консоли (menu)!", show_alert=True)
        return
    try:
        msg = await run(svc.set_auth_mode, target)
    except ServiceError as e:
        await cb.answer(f"❌ {e}"[:200], show_alert=True)
        return
    await cb.answer(msg, show_alert=True)
    st = await run(svc.status)
    await edit(cb, ssh_text(st), ssh_management_keyboard(st.auth_mode))


@router.callback_query(F.data == "settings:ssh_port")
async def ssh_port(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_ssh_port)
    port = (await run(SshService(app).status)).port
    await edit(
        cb,
        (
            "🔑 <b>Изменение порта SSH</b>\n\n"
            f"Текущий порт SSH: <code>{port}</code>\n\n"
            "Введите новый порт SSH (число от 1 до 65535) или нажмите кнопку отмены:"
        ),
        ssh_back_keyboard(),
    )
    await cb.answer()


@router.message(SettingsStates.waiting_ssh_port)
async def ssh_port_msg(message: Message, app: App, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        await message.answer(
            "❌ Неверный формат порта! Порт должен быть числом от 1 до 65535. Введите еще раз:"
        )
        return
    svc = SshService(app)
    port = int(value)
    if port == (await run(svc.status)).port:
        await message.answer("Порт совпадает с текущим. Введите другое число или перейдите в меню:")
        return
    await state.clear()
    try:
        old = await run(svc.change_port, port)
        await run(svc.confirm_port_change, old)  # бот убирает старое правило сразу (как раньше)
        text = (
            f"✅ <b>Успешно!</b>\nПорт SSH изменен на <code>{port}</code>.\n\n"
            "⚠️ <b>Внимание:</b> При следующем подключении не забудьте указать новый порт:\n"
            f"<code>ssh root@&lt;IP&gt; -p {port}</code>"
        )
    except ServiceError as e:
        text = f"❌ <b>Ошибка изменения порта SSH:</b>\n\n<code>{e}</code>"
    await message.answer(text, reply_markup=ssh_back_keyboard(), parse_mode="HTML")


# ─── BBR и обновления ────────────────────────────────────────────────────────

BBR_BOT_TEXT = (  # формулировка бота TransferBox
    "BBR оптимизация успешно отключена (переключено на cubic).",
    "BBR оптимизация отключена (переключено на cubic).",
)


@router.callback_query(F.data == "settings:bbr_toggle")
async def bbr_toggle(cb: CallbackQuery, app: App) -> None:
    try:
        msg = await run(BbrModule(app).toggle)
    except ServiceError as e:
        msg = str(e)
    await cb.answer(msg.replace(*BBR_BOT_TEXT), show_alert=True)
    await edit(cb, SETTINGS_TEXT, settings_menu_keyboard(app.bbr.is_active()))


@router.callback_query(F.data == "settings:check_updates")
async def check_updates(cb: CallbackQuery, app: App) -> None:
    await edit(cb, "⏳ <b>Проверка обновлений панели...</b>")
    await cb.answer()
    try:
        check = await run(UpdateService(app).check)
    except ServiceError as e:
        await edit(
            cb,
            f"❌ <b>Не удалось проверить обновления:</b>\n\n<code>ошибка: {e}</code>",
            settings_update_keyboard(False),
        )
        return
    has_update = check.latest != check.current
    if has_update:
        text = (
            "🔄 <b>Обновление панели</b>\n\n"
            f"Доступна новая версия: <code>{check.latest}</code> "
            f"(ваша: <code>{check.current}</code>).\n\n"
            "Вы можете обновить панель прямо сейчас или через консольное меню сервера."
        )
    else:
        text = (
            "✅ <b>Обновление панели</b>\n\n"
            f"У вас установлена последняя версия панели: <code>{check.current}</code>."
        )
    await edit(cb, text, settings_update_keyboard(has_update))


@router.callback_query(F.data == "settings:run_update")
async def run_update(cb: CallbackQuery, app: App) -> None:
    await edit(
        cb,
        "⏳ <b>Обновление панели запущено...</b>\n\n"
        "Бот выполняет обновление файлов, миграцию базы данных и перезапуск служб. "
        "Это займет несколько секунд, после чего служба бота перезагрузится.",
    )
    await cb.answer()
    svc = UpdateService(app)
    try:
        check = await run(svc.check)
        msg = await run(svc.update, check.latest, False)  # перезапуск — после ответа
    except ServiceError as e:
        await edit(
            cb, f"❌ <b>Ошибка обновления:</b>\n\n<code>{e}</code>", settings_update_keyboard(False)
        )
        return
    await edit(cb, f"✅ <b>{msg}</b>\n\nСлужба бота перезапускается...")
    await run(svc.restart_bot)


# ─── резервные копии ─────────────────────────────────────────────────────────


def backup_text(app: App) -> str:
    b = app.settings.load().backup
    status = "🟢 <b>Автобэкапы:</b> включены" if b.enabled else "🔴 <b>Автобэкапы:</b> отключены"
    when = (
        f"⏰ <b>Время автобэкапа:</b> <code>{b.hour:02d}:{b.minute:02d}</code> каждый день.\n"
        if b.enabled
        else ""
    )
    text = (
        "💾 <b>Резервное копирование</b>\n\n"
        "Архивы бэкапов, отправляемые в Telegram, защищаются паролем, который вы задаете "
        "в TUI.\n\n"
        f"{status}\n{when}"
    )
    if b.password.strip():
        return text + "\n🔐 <b>Пароль бэкапов:</b> задан"
    return text + (
        "\n⚠️ <b>Внимание:</b> Пароль для бэкапов не задан. Бэкапы отправляться "
        "не будут. Задайте его в TUI по SSH."
    )


@router.callback_query(F.data == "settings:backup_menu")
async def backup_menu(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    await edit(cb, backup_text(app), settings_backup_keyboard(app.settings.load().backup.enabled))
    await cb.answer()


@router.callback_query(F.data == "settings:backup_toggle")
async def backup_toggle(cb: CallbackQuery, app: App) -> None:
    enabled = not app.settings.load().backup.enabled
    await run(BackupService(app).set_schedule, enabled)
    b = app.settings.load().backup
    msg = (
        f"Автобэкапы успешно включены (в {b.hour:02d}:{b.minute:02d})."
        if enabled
        else "Автобэкапы успешно отключены."
    )
    await cb.answer(msg, show_alert=True)
    await edit(cb, backup_text(app), settings_backup_keyboard(enabled))


@router.callback_query(F.data == "settings:backup_now")
async def backup_now(cb: CallbackQuery, app: App) -> None:
    b = app.settings.load().backup
    if not b.password.strip():
        await cb.answer(
            "❌ Пароль для бэкапов не задан! Задайте его в TUI по SSH.", show_alert=True
        )
        return
    await edit(cb, "⏳ <b>Создание и отправка резервной копии...</b>")
    await cb.answer()
    try:
        await run(Jobs(app).backup, True)
        text = (
            "✅ <b>Резервная копия успешно отправлена в этот чат!</b>\n\n"
            "Архив зашифрован вашим паролем бэкапов."
        )
    except ServiceError as e:
        text = f"❌ <b>Ошибка резервного копирования:</b>\n\n<code>{e}</code>"
    await edit(cb, text, settings_backup_keyboard(b.enabled))


@router.callback_query(F.data == "settings:backup_change_time")
async def backup_time(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_backup_time)
    b = app.settings.load().backup
    await edit(
        cb,
        (
            "⏰ <b>Изменение времени автобэкапа</b>\n\n"
            f"Текущее время: <code>{b.hour:02d}:{b.minute:02d}</code>\n\n"
            "Введите новое время автоматического бэкапа в формате <code>ЧЧ:ММ</code> "
            "(например, <code>14:30</code> или <code>09:00</code>):"
        ),
        settings_back_keyboard(),
    )
    await cb.answer()


@router.message(SettingsStates.waiting_backup_time)
async def backup_time_msg(message: Message, app: App, state: FSMContext) -> None:
    parts = (message.text or "").strip().split(":")
    if len(parts) != 2:
        await message.answer(
            "❌ Неверный формат! Введите время в формате ЧЧ:ММ (например, 12:00 или 08:30):"
        )
        return
    h, m = parts[0].strip(), parts[1].strip()
    if not h.isdigit() or not m.isdigit():
        await message.answer(
            "❌ Неверный формат! Часы и минуты должны быть числами. "
            "Введите еще раз в формате ЧЧ:ММ:"
        )
        return
    hour, minute = int(h), int(m)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        await message.answer(
            "❌ Недопустимые значения времени! Часы должны быть от 0 до 23, "
            "минуты от 0 до 59. Введите еще раз:"
        )
        return
    await state.clear()
    enabled = app.settings.load().backup.enabled
    try:
        await run(BackupService(app).set_schedule, enabled, hour, minute)
        text = (
            "✅ <b>Время автобэкапа успешно изменено!</b>\n\n"
            f"Новое время: <code>{hour:02d}:{minute:02d}</code> каждый день."
        )
    except Exception as e:
        text = f"❌ <b>Ошибка при изменении расписания cron:</b>\n\n<code>{e}</code>"
    await message.answer(text, reply_markup=settings_backup_keyboard(enabled), parse_mode="HTML")


# ─── шаблон сайта ────────────────────────────────────────────────────────────


def site_text(current: str) -> str:
    label = dict((n, lbl) for lbl, n in SITE_TEMPLATE_LABELS).get(current, current)
    return (
        "🌐 <b>Шаблон сайта</b>\n\n"
        "Сайт, который видят посетители и сканеры на домене сервера. "
        "На работу VPN не влияет.\n\n"
        f"Сейчас: <b>{label}</b>\n\nВыберите шаблон:"
    )


async def _site_screen(cb: CallbackQuery, app: App) -> None:
    current = app.settings.load().site.template
    await edit(cb, site_text(current), site_template_keyboard(current))


@router.callback_query(F.data == "settings:site_menu")
async def site_menu(cb: CallbackQuery, app: App, state: FSMContext) -> None:
    await state.clear()
    await _site_screen(cb, app)
    await cb.answer()


@router.callback_query(F.data.startswith("site:set:"))
async def site_set(cb: CallbackQuery, app: App) -> None:
    name = (cb.data or "").split(":", 2)[2]
    if name not in FAKE_SITE_TEMPLATES:
        await cb.answer("Неизвестный шаблон.", show_alert=True)
        return
    if name == app.settings.load().site.template:
        await cb.answer("Этот шаблон уже выбран.")
        return
    await notify_and_apply(cb, "шаблон сайта", lambda: SettingsService(app).set_site_template(name))
    await _site_screen(cb, app)


# ─── защита от сканеров ──────────────────────────────────────────────────────


def security_text(st: SecurityStatus) -> str:
    bans = {j.name: j.currently_banned for j in st.jails}
    return (
        "🛡️ <b>Защита от сканеров и ботов</b>\n\n"
        f"• Статус службы: {'🟢 <b>Включена</b>' if st.active else '🔴 <b>Отключена</b>'}\n\n"
        "📊 <b>Текущие блокировки:</b>\n"
        f"• Всего заблокировано: <code>{len(st.banned_ips)}</code> IP\n"
        f"• Подбор паролей SSH: <code>{bans.get('sshd', 0)}</code>\n"
        f"• Ловушки портов: <code>{bans.get('portscan-trap', 0)}</code>\n\n"
        "<i>«Разблокировать всех» для экстренного разбана.</i>"
    )


async def _security_screen(cb: CallbackQuery, app: App) -> None:
    st = await run(SecurityService(app).status)
    await edit(cb, security_text(st), security_menu_keyboard(st.active, bool(st.banned_ips)))


# «settings:security_banned_list» — кнопка списка из старых сообщений (список убран, D-033)
@router.callback_query(F.data.in_({"settings:security_menu", "settings:security_banned_list"}))
async def security_menu(cb: CallbackQuery, app: App) -> None:
    await _security_screen(cb, app)
    await cb.answer()


@router.callback_query(F.data == "settings:security_toggle")
async def security_toggle(cb: CallbackQuery, app: App) -> None:
    svc = SecurityService(app)
    ok = True
    try:
        msg = await run(svc.disable if app.fail2ban.is_active() else svc.enable)
    except ServiceError as e:
        ok, msg = False, str(e)
    await cb.answer(msg[:150], show_alert=not ok)
    await _security_screen(cb, app)


@router.callback_query(F.data == "settings:security_unban_all")
async def security_unban_all(cb: CallbackQuery, app: App) -> None:
    msg = await run(SecurityService(app).unban_all)
    await cb.answer(msg[:150], show_alert=True)
    await _security_screen(cb, app)
