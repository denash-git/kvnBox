"""Раздел «Настройки» — порт settings_menu и вложенных экранов TransferBox."""

from __future__ import annotations

import os
import time

from kvnbox.services import ServiceError
from kvnbox.services.apply import Applier
from kvnbox.services.diagnostics import DiagnosticsService
from kvnbox.services.security import SecurityService
from kvnbox.services.settings import SettingsService
from kvnbox.services.ssh import SshService
from kvnbox.services.telegram_bot import BotService
from kvnbox.services.update import UpdateService
from kvnbox.system.fail2ban import SecurityStatus
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import BLUE, BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW
from kvnbox.tui.widgets import BACK

MENU_BIN = "/usr/local/bin/menu"

TEMPLATES = [
    ("Aether Resonance (Эфирный Резонанс)", "aether"),
    ("Quantum Nexus (Квантовый Нексус)", "nexus"),
    ("Synaptic Dynamics (Синаптическая Динамика)", "synapse"),
    ("Chronos Temporal (Хроно-Вектор)", "chronos"),
    ("Stratum Synergy (Стратум Синергия)", "stratum"),
    ("Облако: Nextcloud", "nextcloud"),
    ("Облако: ownCloud Infinite Scale", "ocis"),
]


def settings_menu(m: Menu) -> None:
    while True:
        options = [
            "Изменить Домен и Email",
            "Изменить FQDN имя VPS",
            "Выбрать шаблон маскировочного сайта",
            "Управление SSH",
            "Защита от сканеров (Fail2ban)",
            "Пересоздать конфигурации",
            "Telegram-бот",
            "Проверить обновления панели",
            "Назад",
        ]
        choice = m.select("Настройки", options)
        if choice in (BACK, 8):
            return
        [
            change_domain_email_menu,
            change_vps_fqdn_menu,
            change_site_template_menu,
            ssh_management_menu,
            security_protection_menu,
            apply_settings_menu,
            telegram_bot_menu,
            update_panel_menu,
        ][choice](m)


# ─── домен, FQDN, шаблон, пересоздание ───────────────────────────────────────


def change_domain_email_menu(m: Menu) -> None:
    m.screen("Настройки → Домен и Email")
    s = SettingsService(m.app).get()
    pad = m.ui.block_pad()
    m.ui.out(f"{pad}Текущий домен: {s.domain or 'не задан'}")
    m.ui.out(f"{pad}Текущий Email: {s.admin_email or 'не задан (анонимный SSL)'}")
    m.ui.draw_line("─", BLUE)
    domain = m.ui.read_prompt("Введите новый домен (или Enter для отмены): ").replace(" ", "")
    if domain:
        m.ui.out(f"{pad}{GREEN}✓ Домен изменен на {domain}{RESET}")
    m.ui.out()
    m.ui.out(f"{pad}Для анонимного SSL (без почты) введите символ '-'")
    email = m.ui.read_prompt("Введите новый Email (или Enter для отмены): ").replace(" ", "")
    new_email = None
    if email == "-":
        new_email = ""
        m.ui.out(f"{pad}{GREEN}✓ Email удален (анонимный SSL активирован){RESET}")
    elif email:
        new_email = email
        m.ui.out(f"{pad}{GREEN}✓ Email изменен на {email}{RESET}")
    if domain or new_email is not None:
        m.screen("Настройки → Домен и Email")
        m.progress("Применение настроек...")
        try:
            res = SettingsService(m.app).set_domain_email(
                domain or s.domain, s.admin_email if new_email is None else new_email
            )
            m.show_result(res, "Настройки успешно применены!")
        except ServiceError as e:
            m.ui.out()
            m.fail(str(e))
    m.ui.pause()


def change_vps_fqdn_menu(m: Menu) -> None:
    m.screen("Настройки → FQDN имя VPS")
    pad = m.ui.block_pad()
    current = m.app.runner.run(["hostname", "-f"], timeout=10).stdout.strip()
    m.ui.out(f"{pad}Текущее FQDN имя VPS: {CYAN}{current}{RESET}")
    m.ui.out()
    fqdn = m.ui.read_prompt("Введите новое FQDN имя VPS (или Enter для отмены): ").replace(" ", "")
    if not fqdn:
        m.ui.out()
        m.fail("Отменено. Имя не изменено.")
        m.ui.pause()
        return
    import re

    if not re.fullmatch(r"[A-Za-z0-9.-]+", fqdn):
        m.ui.out()
        m.fail("Неверный формат! Допускаются только буквы, цифры, точки и дефисы.")
        m.ui.pause()
        return
    m.progress("Изменение системного имени хоста...")
    try:
        SettingsService(m.app).set_fqdn(fqdn)
        m.ui.out()
        m.ok(f"FQDN имя VPS успешно изменено на {fqdn}!")
    except ServiceError:
        m.ui.out()
        m.fail("Не удалось изменить имя хоста.")
    m.ui.pause()


def change_site_template_menu(m: Menu) -> None:
    choice = m.select("Выбор шаблона", [t[0] for t in TEMPLATES] + ["Отмена"])
    if choice in (BACK, len(TEMPLATES)):
        return
    m.screen("Настройки → Шаблон сайта")
    m.progress("Применение настроек...")
    try:
        res = SettingsService(m.app).set_site_template(TEMPLATES[choice][1])
        m.show_result(res, "Шаблон успешно изменен!")
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
    m.ui.pause()


def apply_settings_menu(m: Menu) -> None:
    m.screen("Пересоздать конфигурации")
    m.progress("Генерация конфигураций и перезапуск...")
    res = Applier(m.app).apply()
    m.ui.out()
    if res.ok:
        m.ok("Службы успешно перезапущены!")
    else:
        m.ui.center_print(f"{RED}✗ Ошибка:{RESET}")
        for line in res.message.splitlines():
            m.ui.center_print(f"{RED}{line}{RESET}")
    m.ui.pause()


# ─── SSH ─────────────────────────────────────────────────────────────────────


def _ok(m: Menu, text: str) -> None:
    # в разделе SSH текст белый, зелёная только галочка (просьба владельца)
    m.ui.center_print(f"{GREEN}✓{RESET} {text}")


def ssh_management_menu(m: Menu) -> None:
    while True:
        choice = m.select(
            "Управление SSH", ["Изменить порт SSH", "Доступ SSH (Пароль / Ключ)", "Назад"]
        )
        if choice in (BACK, 2):
            return
        if choice == 0:
            change_ssh_port_menu(m)
        else:
            ssh_auth_menu(m)


def change_ssh_port_menu(m: Menu) -> None:
    svc = SshService(m.app)
    cur = svc.status().port
    m.screen("Настройки → Управление SSH → Изменение порта SSH")
    pad = m.ui.block_pad()
    m.ui.out(f"{pad}Текущий порт SSH: {BOLD}{cur}{RESET}")
    m.ui.out(f"{pad}{RED}⚠️ ВНИМАНИЕ: Изменение порта SSH — чувствительное действие.{RESET}")
    m.ui.out(f"{pad}Мы автоматически настроим фаервол UFW, чтобы не заблокировать доступ,")
    m.ui.out(f"{pad}но после изменения настоятельно рекомендуется проверить подключение")
    m.ui.out(f"{pad}в новом окне терминала, не закрывая текущую сессию!\n")
    value = m.ui.read_prompt("Введите новый порт SSH (1-65535, или Enter для отмены): ").strip()
    if not value:
        return
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        m.ui.out()
        m.fail("Неверный порт! Допустимы значения от 1 до 65535.")
        m.ui.pause()
        return
    port = int(value)
    if port == cur:
        m.ui.out()
        _ok(m, "Введенный порт совпадает с текущим.")
        m.ui.pause()
        return
    m.progress("Применение изменений...")
    try:
        old = svc.change_port(port)
    except ServiceError as e:
        m.ui.out()
        m.fail(f"Ошибка: {e}")
        m.ui.pause()
        return
    m.ui.out()
    _ok(m, f"SSH успешно переведен на порт {port}!")
    m.ui.out(f"{pad}{RED}⚠️ ВАЖНО: Откройте НОВОЕ окно терминала и попробуйте подключиться:{RESET}")
    m.ui.out(f"{pad}{BOLD}ssh root@<IP_сервера> -p {port}{RESET}")
    m.ui.out(f"{pad}{RED}НЕ ЗАКРЫВАЙТЕ текущее окно, пока не проверите подключение!{RESET}\n")
    answer = (
        m.ui.read_prompt("Вы успешно подключились по новому порту? (y/n, по умолчанию y): ") or "y"
    )
    if answer in ("y", "Y", "д", "Д"):
        m.progress("Очистка старых правил UFW...")
        svc.confirm_port_change(old)
        _ok(m, f"Правило UFW для старого порта {old} удалено.")
    else:
        m.ui.out()
        m.ui.center_print(
            f"{YELLOW}⚠️ Внимание: старый порт SSH {old} оставлен открытым "
            f"в UFW на всякий случай.{RESET}"
        )
    m.ui.pause()


def _print_keys(m: Menu, pad: str) -> None:
    for k in SshService(m.app).status().keys:
        comment = f" ({k.comment})" if k.comment else ""
        m.ui.out(f"{pad}  [{k.index}] {k.type} {k.fingerprint}{comment}")


def ssh_auth_menu(m: Menu) -> None:
    svc = SshService(m.app)
    while True:
        st = svc.status()
        options = [
            "Отключить вход по паролю" if st.password_allowed else "Включить вход по паролю",
            "Сгенерировать новый ключ SSH",
        ]
        if st.keys:
            options.append("Удалить ключ из списка")
        options.append("Назад")
        choice = m.select("Доступ SSH", options)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel == "Отключить вход по паролю":
            m.screen("Настройки → Отключение входа по паролю")
            if not st.keys:
                m.ui.out()
                m.fail("Нельзя отключить вход по паролю: в системе нет ни одного открытого ключа!")
                m.ui.center_print(f"{DIM}Сначала сгенерируйте ключ SSH.{RESET}")
            else:
                m.progress("Отключение входа по паролю...")
                try:
                    msg = svc.set_auth_mode("key")
                    m.ui.out()
                    _ok(m, msg)
                    m.ui.out()
                    m.ui.center_print(
                        f"{YELLOW}⚠️ Вход по паролю отключен! Доступ возможен "
                        f"только по SSH-ключу.{RESET}"
                    )
                except ServiceError as e:
                    m.ui.out()
                    m.fail(str(e))
            m.ui.out()
            m.ui.pause()
        elif sel == "Включить вход по паролю":
            m.screen("Настройки → Включение входа по паролю")
            m.progress("Включение входа по паролю...")
            try:
                msg = svc.set_auth_mode("password")
                m.ui.out()
                _ok(m, msg)
            except ServiceError as e:
                m.ui.out()
                m.fail(str(e))
            m.ui.out()
            m.ui.pause()
        elif sel == "Сгенерировать новый ключ SSH":
            _generate_key(m, st.port)
        elif sel == "Удалить ключ из списка":
            _delete_key(m, len(st.keys))


def _generate_key(m: Menu, port: int) -> None:
    m.screen("Настройки → Генерация ключа SSH")
    m.progress("Генерация новой пары ключей Ed25519...")
    m.ui.out()
    try:
        private = SshService(m.app).generate_key()
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
        m.ui.out()
        m.ui.pause()
        return
    ip = DiagnosticsService(m.app).public_ip() or "IP_СЕРВЕРА"
    pad = m.ui.block_pad()
    name = private.name
    m.ui.out(
        f"{pad}{GREEN}✓{RESET} Ключ Ed25519 успешно сгенерирован и добавлен в authorized_keys!"
    )
    m.ui.out()
    m.ui.out(f"{pad}Приватный ключ сохранен в файл:")
    m.ui.out(f"{pad}{BOLD}{private.as_posix()}{RESET}")
    m.ui.out()
    m.ui.out(f"{pad}{YELLOW}⚠️ ВАЖНО: Скачайте этот файл на ваш ПК перед отключением пароля!{RESET}")
    m.ui.out(f"{pad}Команда для скачивания (выполнить на вашем ПК):")
    m.ui.out(f"{pad}{DIM}scp -P {port} root@{ip}:{private.as_posix()} ~/.ssh/{name}{RESET}")
    m.ui.out()
    m.ui.out(f"{pad}Команда для подключения к серверу:")
    m.ui.out(f"{pad}{DIM}ssh -i ~/.ssh/{name} -p {port} root@{ip}{RESET}")
    m.ui.out()
    m.ui.pause()


def _delete_key(m: Menu, count: int) -> None:
    m.screen("Настройки → Удаление ключа SSH")
    pad = m.ui.block_pad()
    m.ui.out(f"{pad}Текущие ключи:")
    _print_keys(m, pad)
    m.ui.out()
    m.ui.out(f"{pad}Введите номер ключа для удаления (1-{count}):")
    m.ui.out(f"{pad}{DIM}(или нажмите Enter для отмены){RESET}\n")
    value = m.ui.read_prompt("Номер ключа: ").strip()
    if not value:
        return
    if not value.isdigit() or not 1 <= int(value) <= count:
        m.ui.out()
        m.fail("Неверный номер ключа!")
        m.ui.pause()
        return
    m.progress(f"Удаление ключа #{value}...")
    try:
        msg = SshService(m.app).delete_key(int(value))
        m.ui.out()
        _ok(m, msg)
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
    m.ui.pause()


# ─── защита от сканеров ──────────────────────────────────────────────────────


def _session_ip() -> str:
    import os

    parts = (os.environ.get("SSH_CLIENT") or "").split()
    return parts[0] if parts else ""


def security_protection_menu(m: Menu) -> None:
    svc = SecurityService(m.app)
    while True:
        installed = svc.is_installed()
        st = svc.status() if installed else None
        active = bool(st and st.active)
        my_ip = _session_ip()
        options: list[str] = []
        if active and st is not None:
            options.append("Отключить защиту от сканеров")
            if my_ip:
                options.append(f"Разблокировать мой текущий IP ({my_ip})")
            if sum(j.currently_banned for j in st.jails) > 0:
                options.append("Разблокировать всех")
            options.append("Разблокировать конкретный IP")
        elif installed:
            options.append("Включить защиту от сканеров")
        else:
            options.append("Установить компоненты и включить защиту")
        options.append("Назад")
        choice = m.select("Защита от сканеров", options, _security_info(installed, st))
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel in ("Включить защиту от сканеров", "Установить компоненты и включить защиту"):
            m.screen("Настройки → Активация защиты")
            if not installed:
                m.ui.center_print(
                    f"{DIM}[*] Установка пакетов fail2ban, ipset, python3-systemd...{RESET}"
                )
            m.ui.center_print(f"{DIM}[*] Настройка Fail2ban + Silent Drop (DROP)...{RESET}")
            try:
                msg = svc.enable()
                m.ui.out()
                m.ok(msg)
                time.sleep(2)
            except ServiceError as e:
                m.ui.out()
                m.fail(str(e))
                m.ui.out()
                m.ui.pause()
        elif sel == "Отключить защиту от сканеров":
            m.screen("Настройки → Отключение защиты")
            m.ui.center_print(f"{DIM}[*] Остановка защиты...{RESET}")
            m.ui.out()
            m.ok(svc.disable())
            time.sleep(2)
        elif sel.startswith("Разблокировать мой текущий IP"):
            m.screen("Настройки → Разблокировка текущего IP")
            _unban(m, lambda ip=my_ip: svc.unban(ip))
            time.sleep(2)
        elif sel == "Разблокировать всех":
            m.screen("Настройки → Разблокировка всех IP")
            _unban(m, svc.unban_all)
            time.sleep(2)
        elif sel == "Разблокировать конкретный IP":
            m.screen("Настройки → Разблокировка IP")
            pad = m.ui.block_pad()
            m.ui.out(f"{pad}Введите IP-адрес для снятия блокировки:")
            m.ui.out(f"{pad}{DIM}(или нажмите Enter для отмены){RESET}\n")
            ip = m.ui.read_prompt("IP-адрес: ").strip()
            if ip:
                m.progress(f"Разблокировка {ip}...")
                _unban(m, lambda ip=ip: svc.unban(ip))
                m.ui.pause()


def _security_info(installed: bool, st: SecurityStatus | None) -> list[str]:
    """Состояние и счётчики блокировок — как в Telegram-боте."""
    if not installed:
        return [f"Статус: {RED}● не установлена{RESET}"]
    if st is None or not st.active:
        return [f"Статус: {RED}● отключена{RESET}"]
    bans = {j.name: j.currently_banned for j in st.jails}
    return [
        f"Статус: {GREEN}● включена{RESET}",
        "",
        "Текущие блокировки:",
        f"  • Всего заблокировано: {BOLD}{len(st.banned_ips)}{RESET} IP",
        f"  • Подбор паролей SSH: {BOLD}{bans.get('sshd', 0)}{RESET}",
        f"  • Ловушки портов: {BOLD}{bans.get('portscan-trap', 0)}{RESET}",
    ]


def _unban(m: Menu, action: object) -> None:
    try:
        msg = action()  # type: ignore[operator]
        m.ui.out()
        m.ok(str(msg))
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))


# ─── Telegram-бот ────────────────────────────────────────────────────────────


def telegram_bot_menu(m: Menu) -> None:
    svc = BotService(m.app)
    while True:
        svc.ensure_unit()
        st = svc.state()
        options = [
            "Настроить Токен и Chat ID",
            "Отключить Telegram-бот" if st.enabled else "Включить Telegram-бот",
            "Отключить оповещения" if st.alerts else "Включить оповещения",
            "Проверить отправку сообщений",
            "Назад",
        ]
        choice = m.select("Настройки → Telegram-бот", options)
        if choice in (BACK, 4):
            return
        sel = options[choice]
        if sel == "Настроить Токен и Chat ID":
            m.screen("Настройки → Telegram-бот → Настройка")
            m.ui.center_print("Инструкция:")
            m.ui.center_print(
                "1. Напишите @BotFather в Telegram, отправьте /newbot и получите Токен."
            )
            m.ui.center_print("2. Напишите @userinfobot в Telegram и получите свой CHAT ID.")
            m.ui.center_print(
                f"3. {YELLOW}ОБЯЗАТЕЛЬНО найдите вашего нового бота в Telegram по имени{RESET}"
            )
            m.ui.center_print(
                f"   {YELLOW}и нажмите кнопку Запустить (/start), иначе сообщения не придут!{RESET}"
            )
            m.ui.out()
            token = m.ui.read_prompt("Введите Токен бота: ")
            if not token:
                continue
            chat = m.ui.read_prompt("Введите Chat ID: ")
            if not chat:
                continue
            try:
                svc.configure(token, chat)
                m.ui.out()
                m.ok("Настройки успешно сохранены!")
            except ServiceError as e:
                m.ui.out()
                m.fail(str(e))
            m.ui.pause()
        elif sel == "Включить Telegram-бот":
            m.screen("Настройки → Telegram-бот → Включение")
            if not st.configured:
                m.ui.out()
                m.fail("Ошибка: Сначала настройте Токен и Chat ID!")
                m.ui.pause()
                continue
            m.progress("Создание службы systemd...")
            m.progress("Запуск и активация службы...")
            m.progress("Установка расписания Cron для алертов...")
            delivered = svc.enable()
            time.sleep(2)
            m.progress("Отправка тестового оповещения...")
            m.ui.out()
            if delivered:
                m.ok("Бот успешно запущен! Тестовое сообщение отправлено.")
            else:
                m.ui.center_print(
                    f"{YELLOW}⚠️ Бот включен, но не удалось отправить тестовое "
                    f"сообщение. Проверьте правильность токена/ID.{RESET}"
                )
            m.ui.pause()
        elif sel == "Отключить Telegram-бот":
            m.screen("Настройки → Telegram-бот → Отключение")
            m.progress("Остановка и отключение службы...")
            m.progress("Удаление cron-задач...")
            svc.disable()
            m.ui.out()
            m.ok("Telegram-бот успешно отключен.")
            m.ui.pause()
        elif sel in ("Включить оповещения", "Отключить оповещения"):
            m.screen("Настройки → Telegram-бот → Оповещения")
            on = svc.toggle_alerts()
            m.ui.out()
            m.ok(f"Оповещения успешно {'включены' if on else 'отключены'}.")
            m.ui.pause()
        elif sel == "Проверить отправку сообщений":
            m.screen("Настройки → Telegram-бот → Тест")
            m.progress("Отправка тестового сообщения...")
            m.ui.out()
            if svc.send_test():
                m.ok("Сообщение успешно доставлено!")
            else:
                m.fail("Не удалось отправить сообщение. Проверьте настройки бота и лог ошибок.")
            m.ui.pause()


# ─── обновление панели ───────────────────────────────────────────────────────


def update_panel_menu(m: Menu) -> None:
    m.screen("Настройки → Обновление панели")
    m.ui.center_print(f"{DIM}[*] Проверка обновлений на GitHub...{RESET}")
    svc = UpdateService(m.app)
    try:
        check = svc.check()
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
        m.ui.pause()
        return
    m.ui.center_print(f"Текущая версия: {CYAN}{check.current}{RESET}")
    m.ui.center_print(f"Свежая версия:  {CYAN}{check.latest}{RESET}")
    if check.up_to_date:
        m.ui.out()
        m.ok("У вас установлена актуальная версия панели.")
        m.ui.pause()
        return
    if not check.newer_available:
        m.ui.out(
            f"\n  {YELLOW}⚠️ Ваша версия ({check.current}) новее, чем версия в "
            f"репозитории ({check.latest}).{RESET}"
        )
        m.ui.pause()
        return
    m.ui.out()
    m.ui.center_print(f"Доступно обновление до версии {GREEN}{check.latest}{RESET}.")
    answer = m.ui.read_prompt("Обновить панель? [y/N]: ")
    if answer in ("y", "Y", "д", "Д"):
        m.screen("Настройки → Обновление панели")
        m.ui.center_print(f"{DIM}[*] Установка обновлений...{RESET}")
        try:
            msg = svc.update(check.latest)
        except ServiceError as e:
            m.ui.out()
            m.fail(str(e))
            m.ui.pause()
            return
        svc.restart_bot()
        m.ui.out()
        m.ok(msg)
        m.ui.center_print("Панель управления будет перезапущена...")
        m.ui.pause()
        os.execv(MENU_BIN, [MENU_BIN])  # новая версия кода
