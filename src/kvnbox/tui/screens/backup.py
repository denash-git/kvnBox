"""Раздел «Бэкап и Восстановление» — порт backup_restore_menu."""

from __future__ import annotations

import datetime as dt

from kvnbox.services import ServiceError
from kvnbox.services.backup import BackupService, PasswordRequired
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import BOLD, DIM, RED, RESET, YELLOW
from kvnbox.tui.widgets import BACK


def backup_restore_menu(m: Menu) -> None:
    svc = BackupService(m.app)
    while True:
        choice = m.select(
            "Бэкап и Восстановление",
            [
                "Создать бэкап настроек",
                "Восстановить из последнего бэкапа",
                "Задать пароль для бэкапов",
                "Назад",
            ],
        )
        if choice in (BACK, 3):
            return
        if choice == 0:
            m.screen("Создание резервной копии")
            try:
                path = svc.create()
                m.ui.out()
                m.ok("Бэкап успешно создан!")
                m.ui.center_print(f"Файл сохранен: {BOLD}{path.as_posix()}{RESET}")
                m.ui.center_print(
                    "Этот бэкап не зашифрован и может быть быстро восстановлен локально."
                )
            except ServiceError as e:
                m.ui.out()
                m.fail(f"Ошибка создания бэкапа: {e}")
            m.ui.pause()
        elif choice == 1:
            _restore(m, svc)
        elif choice == 2:
            _set_password(m, svc)


def _restore(m: Menu, svc: BackupService) -> None:
    m.screen("Восстановление из последнего бэкапа")
    latest = svc.latest()
    if latest is None:
        m.ui.out()
        m.fail(f"Ошибка: В папке {svc.directory.as_posix()} не найдено файлов бэкапов!")
        m.ui.pause()
        return
    pad = m.ui.block_pad()
    date = dt.datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    m.ui.out(f"{pad}Найден последний файл бэкапа:")
    m.ui.out(f"{pad}• Имя:  {BOLD}{latest.name}{RESET}")
    m.ui.out(f"{pad}• Дата: {date}")
    m.ui.out()
    answer = m.ui.read_prompt(
        "Восстановить настройки из этого файла? Текущие данные будут перезаписаны! [y/N]: "
    )
    if answer not in ("y", "Y"):
        m.ui.out()
        m.ui.center_print("Операция отменена.")
        m.ui.pause()
        return
    m.progress("Проверка архива и восстановление...")
    password: str | None = None
    while True:
        try:
            result = svc.restore(latest, password)
            break
        except PasswordRequired:
            if password is not None:
                m.ui.out()
                m.fail("Ошибка при распаковке архива: неверный пароль.")
                m.ui.pause()
                return
            password = m.ui.read_prompt("Архив защищен паролем. Введите пароль для расшифровки: ")
        except ServiceError as e:
            m.ui.out()
            m.fail(f"Ошибка при распаковке архива: {e}")
            m.ui.pause()
            return
    for line in result.log:
        m.ui.center_print(f"{DIM}[*] {line}{RESET}")
    m.ui.out()
    if result.ok:
        m.ok("Настройки успешно восстановлены и применены!")
        m.ui.center_print("Все службы запущены и функционируют на восстановленных данных.")
        if any(line.startswith("имя хоста:") for line in result.log):
            fqdn = next(line for line in result.log if line.startswith("имя хоста:")).split(": ")[1]
            m.ui.out()
            m.ui.center_print(f"{YELLOW}⚠️ Имя хоста изменено на '{fqdn}'.{RESET}")
            m.ui.center_print(
                f"{YELLOW}Переподключитесь по SSH или выполните 'exec bash' после выхода,{RESET}"
            )
            m.ui.center_print(f"{YELLOW}чтобы обновить имя в приглашении командной строки.{RESET}")
    else:
        m.fail(f"Ошибка при применении восстановленных настроек: {result.message}")
    m.ui.pause()


def _set_password(m: Menu, svc: BackupService) -> None:
    m.screen("Задать пароль для бэкапов")
    m.ui.out()
    current = m.app.settings.load().backup.password.strip()
    m.ui.center_print(f"Текущий пароль для отправки в Telegram: {current or 'не задан'}")
    m.ui.out()
    value = m.ui.read_prompt(
        "Введите новый пароль (не менее 4 символов, или Enter для отмены): "
    ).strip()
    m.ui.out()
    if not value:
        m.ui.center_print(f"{YELLOW}Изменения отменены.{RESET}")
    elif len(value) < 4:
        m.ui.center_print(f"{RED}❌ Пароль слишком короткий (минимум 4 символа).{RESET}")
    else:
        svc.set_password(value)
        m.ok("Пароль для бэкапов успешно сохранен!")
    m.ui.pause()
