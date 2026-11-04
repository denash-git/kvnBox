"""Раздел «Модули» — порт modules_menu, singbox/mieru/netbird_module_menu,
change_vless_paths_menu, bbr_optimization_menu, update_singbox_menu."""

from __future__ import annotations

from kvnbox.services import ServiceError
from kvnbox.services.modules import BbrModule, MieruModule, NetbirdModule, SingboxModule
from kvnbox.services.settings import SettingsService
from kvnbox.tui.header import netbird_status
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import BOLD, DIM, GREEN, RED, RESET, YELLOW
from kvnbox.tui.widgets import BACK


def _on(flag: bool) -> str:
    return f"{GREEN}включен{RESET}" if flag else f"{RED}отключен{RESET}"


def modules_menu(m: Menu) -> None:
    while True:
        settings = m.app.settings.load()
        nb_state = NetbirdModule(m.app).connection_state()
        bbr = m.app.bbr.is_active()
        options = [
            f"Mieru       [{_on(settings.mieru.enabled)}]",
            f"NetBird     [{netbird_status(nb_state, dot=False)}]",
            f"sing-box    [{_on(m.app.systemd.is_active('sing-box'))}]",
            "Настроить пути VLESS",
            "Отключить BBR оптимизацию" if bbr else "Включить BBR оптимизацию",
            "Назад",
        ]
        choice = m.select("Модули", options)
        if choice in (BACK, 5):
            return
        [
            mieru_module_menu,
            netbird_module_menu,
            singbox_module_menu,
            change_vless_paths_menu,
            bbr_optimization_menu,
        ][choice](m)


# ─── простые действия со службой ─────────────────────────────────────────────


def _service_action(m: Menu, title: str, doing: str, done: str, action: object) -> None:
    m.screen(title)
    m.progress(doing)
    action()  # type: ignore[operator]
    m.ui.out()
    m.ok(done)
    m.ui.pause()


def _logs(m: Menu, title: str, unit: str) -> None:
    m.screen(title)
    m.ui.t.write(m.app.systemd.journal(unit, 50))
    m.ui.pause()


# ─── sing-box ────────────────────────────────────────────────────────────────


def singbox_module_menu(m: Menu) -> None:
    mod = SingboxModule(m.app)
    while True:
        active = m.app.systemd.is_active("sing-box")
        options = ["Остановить службу", "Перезапустить службу"] if active else ["Запустить службу"]
        options += ["Просмотреть логи службы", "Обновить sing-box", "Назад"]
        choice = m.select("Модуль: sing-box", options)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel == "Запустить службу":
            _service_action(
                m,
                "Модули → sing-box → Запуск",
                "Запуск службы sing-box...",
                "Служба sing-box запущена.",
                mod.start,
            )
        elif sel == "Остановить службу":
            _service_action(
                m,
                "Модули → sing-box → Остановка",
                "Остановка службы sing-box...",
                "Служба sing-box остановлена.",
                mod.stop,
            )
        elif sel == "Перезапустить службу":
            _service_action(
                m,
                "Модули → sing-box → Перезапуск",
                "Перезапуск службы sing-box...",
                "Служба sing-box перезапущена.",
                mod.restart,
            )
        elif sel == "Просмотреть логи службы":
            _logs(m, "Модули → sing-box → Логи службы", "sing-box")
        elif sel == "Обновить sing-box":
            update_singbox_menu(m)


def update_singbox_menu(m: Menu) -> None:
    mod = SingboxModule(m.app)
    m.screen("Модули → sing-box → Обновление")
    m.progress("Получение информации о версиях...")
    current = mod.info().version or "не установлен"
    try:
        latest = mod.latest_version()
    except ServiceError:
        m.ui.out()
        m.fail("Ошибка: Не удалось получить последнюю версию с GitHub.")
        m.ui.pause()
        return
    pad = m.ui.block_pad()
    m.ui.out()
    m.ui.out(f"{pad}Текущая установленная версия: {BOLD}{current}{RESET}")
    m.ui.out(f"{pad}Последняя официальная версия: {BOLD}{latest}{RESET}\n")
    if current == latest:
        m.ui.out(f"{pad}{GREEN}✓ У вас уже установлена последняя официальная версия.{RESET}")
        answer = m.ui.read_prompt(
            f"Хотите переустановить/принудительно обновить версию {latest}? [y/N]: "
        )
    else:
        m.ui.out(f"{pad}{YELLOW}⚠ Доступно обновление до версии {latest}!{RESET}")
        answer = m.ui.read_prompt("Продолжить обновление? [y/N]: ")
    if answer not in ("y", "Y", "д", "Д"):
        m.ui.out()
        m.ui.center_print("Обновление отменено.")
        m.ui.pause()
        return
    m.screen("Модули → sing-box → Обновление")
    m.progress(f"Запуск процесса обновления до версии {latest}...")
    try:
        res = mod.update()
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
        m.ui.pause()
        return
    m.ui.out()
    m.ok("sing-box успешно установлен/обновлен.")
    m.progress("Проверка совместимости конфигурации...")
    m.ui.out()
    if res.ok:
        m.ok("Служба sing-box успешно обновлена и перезапущена.")
    else:
        m.fail("ВНИМАНИЕ: Новая версия не совместима с вашим конфигом!")
        m.ui.center_print(
            f"{RED}Служба не будет перезапущена, чтобы сохранить работоспособность.{RESET}"
        )
    m.ui.pause()


# ─── Mieru ───────────────────────────────────────────────────────────────────


def _install_mieru(m: Menu, mod: MieruModule) -> None:
    m.progress("Поиск последней версии mita (Mieru)...")
    m.ui.center_print(f"{DIM}[*] Установка пакета...{RESET}")
    try:
        mod.install()
        m.ui.out()
        m.ok("Mieru (Mita) успешно установлен!")
    except ServiceError:
        m.ui.out()
        m.fail("Не удалось скачать пакет Mieru. Проверьте интернет-соединение.")


def mieru_module_menu(m: Menu) -> None:
    mod = MieruModule(m.app)
    while True:
        if not m.app.installed("mita"):
            choice = m.select("Модуль: Mieru", ["Установить Mieru", "Назад"])
            if choice in (BACK, 1):
                return
            m.screen("Модули → Mieru → Установка")
            _install_mieru(m, mod)
            m.ui.pause()
            continue
        s = m.app.settings.load().mieru
        options = (
            ["Остановить службу", "Перезапустить службу"] if s.enabled else ["Запустить службу"]
        )
        port_item = f"Изменить порт (Текущий порт: {s.port})"
        options += [port_item, "Обновить Mieru", "Удалить Mieru", "Назад"]
        choice = m.select("Модуль: Mieru", options)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel in ("Запустить службу", "Остановить службу"):
            start = sel == "Запустить службу"
            m.screen(f"Модули → Mieru → {'Запуск' if start else 'Остановка'}")
            m.progress(f"{'Запуск' if start else 'Остановка'} службы Mieru...")
            res = mod.set_enabled(start)
            m.show_result(
                res,
                "Служба Mieru запущена. Сервисы перезапущены."
                if start
                else "Служба Mieru остановлена. Сервисы перезапущены.",
            )
            m.ui.pause()
        elif sel == "Перезапустить службу":
            _service_action(
                m,
                "Модули → Mieru → Перезапуск",
                "Перезапуск службы Mieru...",
                "Служба Mieru перезапущена.",
                mod.restart,
            )
        elif sel == port_item:
            m.screen("Модули → Mieru → Изменение порта")
            value = m.ui.read_prompt(
                f"Введите новый порт для Mieru (1-65535, по умолчанию {s.port}): "
            ).strip()
            value = value or str(s.port)
            if not value.isdigit() or not 1 <= int(value) <= 65535:
                m.ui.out()
                m.fail("Неверный порт! Допустимы значения от 1 до 65535.")
                m.ui.pause()
                continue
            m.progress("Сохранение настроек порта Mieru...")
            res = mod.set_port(int(value))
            m.show_result(res, f"Порт Mieru изменен на {value}. Сервисы перезапущены.")
            m.ui.pause()
        elif sel == "Обновить Mieru":
            m.screen("Модули → Mieru → Обновление")
            _install_mieru(m, mod)
            m.ui.pause()
        elif sel == "Удалить Mieru":
            m.screen("Модули → Mieru → Удаление")
            m.ui.out()
            m.ui.center_print(
                f"{RED}⚠️ ВНИМАНИЕ: Это полностью удалит Mieru с этого сервера!{RESET}"
            )
            m.ui.out()
            if m.ui.read_prompt("Вы уверены, что хотите удалить Mieru? (y/N): ") in ("y", "Y"):
                m.progress("Остановка и отключение службы...")
                m.ui.center_print(f"{DIM}[*] Удаление пакета...{RESET}")
                mod.uninstall()
                m.ui.out()
                m.ok("Mieru (Mita) успешно удален.")
            else:
                m.ui.out()
                m.ui.center_print("Удаление отменено.")
            m.ui.pause()


# ─── NetBird ─────────────────────────────────────────────────────────────────


def netbird_module_menu(m: Menu) -> None:
    mod = NetbirdModule(m.app)
    while True:
        if not m.app.installed("netbird"):
            choice = m.select("Модуль: NetBird", ["Установить NetBird клиент", "Назад"])
            if choice in (BACK, 1):
                return
            m.screen("Модули → NetBird → Установка")
            m.progress("Начало установки NetBird клиента...")
            m.ui.center_print(f"{DIM}[*] Загрузка и запуск официального инсталлятора...{RESET}")
            try:
                mod.install()
                m.ui.out()
                m.ok("NetBird клиент успешно установлен!")
            except ServiceError:
                m.ui.out()
                m.fail("Ошибка при установке NetBird клиента.")
            m.ui.pause()
            continue
        nb = m.app.settings.load().netbird
        active = m.app.systemd.is_active("netbird")
        key_lbl = (
            f"Setup Key {GREEN}[задан]{RESET}"
            if nb.setup_key
            else f"Setup Key {RED}[не задан]{RESET}"
        )
        url_lbl = (
            f"Management URL {GREEN}[задан]{RESET}"
            if nb.management_url
            else f"Management URL {RED}[не задан]{RESET}"
        )
        options = [key_lbl, url_lbl, "Выполнить регистрацию и запуск (netbird up)"]
        options += ["Остановить службу", "Перезапустить службу"] if active else ["Запустить службу"]
        options += [
            "Показать подробный статус",
            "Просмотреть логи службы",
            "Обновить NetBird клиент",
            "Удалить NetBird клиент",
            "Назад",
        ]
        choice = m.select("Модуль: NetBird", options)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if choice == 0:
            m.screen("Модули → NetBird → Ввод Setup Key")
            key = m.ui.read_prompt("Введите Setup Key (или Enter для отмены): ")
            m.ui.out()
            if key:
                try:
                    mod.set_setup_key(key)
                    m.ok("Setup Key успешно сохранен.")
                except ServiceError as e:
                    m.fail(str(e))
            else:
                m.fail("Отменено. Ключ не изменен.")
            m.ui.pause()
        elif choice == 1:
            m.screen("Модули → NetBird → Ввод Management URL")
            url = m.ui.read_prompt("Введите Management URL (или Enter для отмены): ")
            m.ui.out()
            if url:
                try:
                    mod.set_management_url(url)
                    m.ok("Management URL успешно сохранен.")
                except ServiceError as e:
                    m.fail(str(e))
            else:
                m.fail("Отменено. URL не изменен.")
            m.ui.pause()
        elif sel == "Выполнить регистрацию и запуск (netbird up)":
            m.screen("Модули → NetBird → Регистрация")
            if not nb.setup_key:
                m.ui.out()
                m.fail("Ошибка: Сначала необходимо указать Setup Key!")
                m.ui.pause()
                continue
            m.progress("Запуск регистрации пира в сети NetBird...")
            cmd = f"netbird up --setup-key {nb.setup_key}" + (
                f" --management-url {nb.management_url}" if nb.management_url else ""
            )
            m.ui.center_print(f"{DIM}[*] Выполняется: {cmd}{RESET}")
            res = mod.up()
            m.ui.t.write(res.stdout + res.stderr)
            m.ui.out()
            m.ok("Команда подключения выполнена!") if res.ok else m.fail("Ошибка при регистрации.")
            m.ui.pause()
        elif sel == "Запустить службу":
            _service_action(
                m,
                "Модули → NetBird → Запуск",
                "Запуск службы NetBird...",
                "Служба NetBird запущена.",
                mod.start,
            )
        elif sel == "Остановить службу":
            _service_action(
                m,
                "Модули → NetBird → Остановка",
                "Остановка службы NetBird...",
                "Служба NetBird остановлена.",
                mod.stop,
            )
        elif sel == "Перезапустить службу":
            _service_action(
                m,
                "Модули → NetBird → Перезапуск",
                "Перезапуск службы NetBird...",
                "Служба NetBird перезапущена.",
                mod.restart,
            )
        elif sel == "Показать подробный статус":
            m.screen("Модули → NetBird → Подробный статус")
            if active:
                m.ui.out(mod.detailed_status())
            else:
                m.ui.out()
                m.ui.center_print(
                    f"{RED}Служба NetBird остановлена. Подробная информация недоступна.{RESET}"
                )
            m.ui.pause()
        elif sel == "Просмотреть логи службы":
            _logs(m, "Модули → NetBird → Логи службы", "netbird")
        elif sel == "Обновить NetBird клиент":
            m.screen("Модули → NetBird → Обновление")
            m.progress("Обновление NetBird клиента из официального репозитория...")
            res = mod.update()
            m.ui.out()
            m.ok("NetBird клиент успешно обновлен!") if res.ok else m.fail(
                "Ошибка при обновлении NetBird."
            )
            m.ui.pause()
        elif sel == "Удалить NetBird клиент":
            m.screen("Модули → NetBird → Удаление")
            m.ui.out()
            m.ui.center_print(
                f"{RED}⚠️ ВНИМАНИЕ: Это полностью удалит NetBird с этого сервера!{RESET}"
            )
            m.ui.out()
            if m.ui.read_prompt("Вы уверены, что хотите удалить NetBird? (y/N): ") in ("y", "Y"):
                m.progress("Остановка службы...")
                m.ui.center_print(f"{DIM}[*] Удаление пакета...{RESET}")
                m.ui.center_print(f"{DIM}[*] Удаление репозитория...{RESET}")
                mod.uninstall()
                m.ui.out()
                m.ok("NetBird клиент успешно удален.")
            else:
                m.ui.out()
                m.ui.center_print("Удаление отменено.")
            m.ui.pause()


# ─── пути VLESS и BBR ────────────────────────────────────────────────────────


def change_vless_paths_menu(m: Menu) -> None:
    svc = SettingsService(m.app)
    while True:
        v = svc.get().vless
        options = [
            f"Путь VLESS WS:      {v.ws_path}",
            f"Сервис VLESS gRPC:  {v.grpc_service or '(пусто)'}",
            f"Путь VLESS XHTTP:   {v.httpupgrade_path}",
            "Назад",
        ]
        choice = m.select("Настройки путей", options)
        if choice in (BACK, 3):
            return
        m.screen("Модули → Пути VLESS")
        m.ui.out()
        prompts = [
            "Введите путь WS (должен начинаться со слэша /): ",
            "Введите имя gRPC сервиса (можно пустой): ",
            "Введите путь XHTTP (должен начинаться со слэша /): ",
        ]
        value = m.ui.read_prompt(prompts[choice]).replace(" ", "")
        if choice != 1 and not value.startswith("/"):
            m.ui.out()
            m.fail("Некорректный путь (должен начинаться со слэша /)")
            m.ui.pause()
            continue
        m.screen("Модули → Пути VLESS")
        m.progress("Применение настроек...")
        try:
            kwargs = [{"ws_path": value}, {"grpc_service": value}, {"httpupgrade_path": value}][
                choice
            ]
            res = svc.set_vless_paths(**kwargs)
            m.show_result(res, "Настройки успешно применены!")
        except ServiceError as e:
            m.ui.out()
            m.fail(str(e))
        m.ui.pause()


def bbr_optimization_menu(m: Menu) -> None:
    m.screen("Модули → BBR Оптимизация")
    active = m.app.bbr.is_active()
    m.progress("Отключение BBR оптимизации..." if active else "Включение BBR оптимизации...")
    try:
        msg = BbrModule(m.app).toggle()
        m.ui.out()
        m.ok(msg)
    except ServiceError as e:
        m.ui.out()
        m.fail(f"Ошибка: {e}" if not active else str(e))
    m.ui.pause()
