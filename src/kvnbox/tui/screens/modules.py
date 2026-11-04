"""Раздел «Модули» — порт modules_menu, singbox/mieru/netbird_module_menu,
change_vless_paths_menu, bbr_optimization_menu, update_singbox_menu."""

from __future__ import annotations

import contextlib
import subprocess

from kvnbox.core.models import CoreKind, Transport
from kvnbox.services import ServiceError
from kvnbox.services.modules import (
    BbrModule,
    CaddyModule,
    MieruModule,
    NetbirdModule,
    SingboxModule,
    XrayModule,
)
from kvnbox.services.settings import SettingsService
from kvnbox.tui.header import netbird_status, xray_status
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
            f"Caddy       [{_on(m.app.systemd.is_active('caddy'))}]",
            f"Xray        [{_xray_badge(m)}]",
            "Настроить пути протоколов",
            "Настройки CDN",
            "Отключить BBR оптимизацию" if bbr else "Включить BBR оптимизацию",
            "Назад",
        ]
        choice = m.select("Модули", options)
        if choice in (BACK, 8):
            return
        [
            mieru_module_menu,
            netbird_module_menu,
            singbox_module_menu,
            caddy_module_menu,
            xray_module_menu,
            change_vless_paths_menu,
            cdn_settings_menu,
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
            update_core_menu(m, "Модули → sing-box → Обновление", mod)


def _confirm_versions(m: Menu, current: str, latest: str) -> bool:
    """Текущая и свежая версия + подтверждение (ядра обновляются только вручную)."""
    pad = m.ui.block_pad()
    m.ui.out()
    m.ui.out(f"{pad}Текущая установленная версия: {BOLD}{current or 'не установлен'}{RESET}")
    m.ui.out(f"{pad}Последняя официальная версия: {BOLD}{latest}{RESET}\n")
    if current.removeprefix("v") == latest.removeprefix("v"):
        m.ui.out(f"{pad}{GREEN}✓{RESET} У вас уже установлена последняя официальная версия.")
        question = f"Переустановить версию {latest}? [y/N]: "
    else:
        m.ui.out(f"{pad}{YELLOW}⚠ Доступна версия {latest}.{RESET}")
        m.ui.out(f"{pad}{DIM}Если новая версия не заработает, вернётся прежняя.{RESET}\n")
        question = "Обновить? [y/N]: "
    if m.ui.read_prompt(question) in ("y", "Y", "д", "Д"):
        return True
    m.ui.out()
    m.ui.center_print("Обновление отменено.")
    m.ui.pause()
    return False


def update_core_menu(m: Menu, title: str, mod: SingboxModule | MieruModule | XrayModule) -> None:
    """Обновление sing-box / Mieru: проверка суммы, применение, возврат при сбое."""
    m.screen(title)
    m.progress("Получение информации о версиях...")
    try:
        latest = mod.latest_version()
    except ServiceError:
        m.ui.out()
        m.fail("Ошибка: Не удалось получить последнюю версию с GitHub.")
        m.ui.pause()
        return
    if not _confirm_versions(m, mod.info().version, latest):
        return
    m.screen(title)
    m.progress(f"Скачивание и проверка версии {latest}...")
    m.ui.center_print(f"{DIM}[*] Установка, проверка конфигурации и перезапуск...{RESET}")
    try:
        res = mod.safe_update()
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
        m.ui.center_print(f"{DIM}Ничего не изменено.{RESET}")
        m.ui.pause()
        return
    m.ui.out()
    if res.ok:
        m.ok(res.message)
    else:
        m.fail("Обновление не удалось.")
        m.ui.center_print(res.message)
    m.ui.pause()


# ─── Xray (этап 11) ──────────────────────────────────────────────────────────


def _xray_badge(m: Menu) -> str:
    if not m.app.installed("xray"):
        return f"{DIM}не установлен{RESET}"
    return xray_status(m.app, dot=False)


def xray_module_menu(m: Menu) -> None:
    mod = XrayModule(m.app)
    while True:
        if not m.app.installed("xray"):
            choice = m.select("Модуль: Xray", ["Установить Xray", "Назад"])
            if choice in (BACK, 1):
                return
            m.screen("Модули → Xray → Установка")
            m.progress("Скачивание и проверка Xray...")
            try:
                res = mod.install()
                m.show_result(res, "Xray установлен. Он включится с первым подключением на Xray.")
            except ServiceError as e:
                m.ui.out()
                m.fail(str(e))
            m.ui.pause()
            continue
        enabled = m.app.settings.load().xray.enabled
        count = mod.connections_count()
        options = ["Отключить Xray" if enabled else "Включить Xray"]
        if m.app.systemd.is_active("xray"):
            options.append("Перезапустить службу")
        options += ["Просмотреть логи службы", "Обновить Xray"]
        if count == 0:
            options.append("Удалить Xray")
        options.append("Назад")
        info = [f"Версия: {BOLD}{mod.info().version or '?'}{RESET}    Подключений на Xray: {count}"]
        choice = m.select("Модуль: Xray", options, info)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel == "Отключить Xray":
            m.screen("Модули → Xray → Отключение")
            m.ui.out()
            if count:
                m.ui.center_print(
                    f"{YELLOW}Подключения на Xray ({count}) не будут работать до включения.{RESET}"
                )
            m.ui.center_print(f"{DIM}Xray не удаляется: включить снова — этот же пункт.{RESET}")
            m.ui.out()
            if m.ui.read_prompt("Отключить Xray? (y/N): ") in ("y", "Y", "д", "Д"):
                m.progress("Отключение Xray...")
                m.show_result(mod.set_enabled(False), "Xray отключен.")
            else:
                m.ui.out()
                m.ui.center_print("Отключение отменено.")
            m.ui.pause()
        elif sel == "Включить Xray":
            m.screen("Модули → Xray → Включение")
            m.progress("Включение Xray...")
            m.show_result(
                mod.set_enabled(True),
                "Xray включен." + ("" if count else " Служба запустится с первым подключением."),
            )
            m.ui.pause()
        elif sel == "Перезапустить службу":
            _service_action(
                m,
                "Модули → Xray → Перезапуск",
                "Перезапуск службы Xray...",
                "Служба Xray перезапущена.",
                mod.restart,
            )
        elif sel == "Просмотреть логи службы":
            _logs(m, "Модули → Xray → Логи службы", "xray")
        elif sel == "Обновить Xray":
            update_core_menu(m, "Модули → Xray → Обновление", mod)
        elif sel == "Удалить Xray":
            m.screen("Модули → Xray → Удаление")
            m.ui.out()
            if m.ui.read_prompt("Удалить Xray с сервера? (y/N): ") in ("y", "Y", "д", "Д"):
                m.progress("Удаление Xray...")
                try:
                    m.show_result(mod.uninstall(), "Xray удален.")
                except ServiceError as e:
                    m.ui.out()
                    m.fail(str(e))
            m.ui.pause()


def cdn_settings_menu(m: Menu) -> None:
    """Протокол CDN: домен для клиентов и origin-поддомен, с которым приходит CDN."""
    svc = SettingsService(m.app)
    c = svc.get().cdn
    m.screen("Модули → Настройки CDN")
    if not m.app.installed("xray"):
        m.ui.out()
        m.fail("Протокол CDN работает на ядре Xray — сначала установите его (Модули → Xray).")
        m.ui.pause()
        return
    pad = m.ui.block_pad()
    m.ui.out(f"{pad}Домен CDN (в ссылках клиентов): {BOLD}{c.domain or '(не задан)'}{RESET}")
    m.ui.out(
        f"{pad}Origin-поддомен (с ним приходит CDN): {BOLD}{c.origin_host or '(не задан)'}{RESET}"
    )
    m.ui.out()
    m.ui.out(f"{pad}{DIM}Origin-поддомен должен указывать на IP этого сервера (A-запись):{RESET}")
    m.ui.out(f"{pad}{DIM}на него будет получен отдельный сертификат.{RESET}")
    m.ui.out(f"{pad}{DIM}Enter — оставить как есть, «-» — очистить.{RESET}")
    m.ui.out()
    domain = m.ui.read_prompt("Домен CDN (например, cdn.example.com): ").strip()
    origin = m.ui.read_prompt("Origin-поддомен (например, xxx.example.com): ").strip()
    if not domain and not origin:
        return
    domain = "" if domain == "-" else (domain or c.domain)
    origin = "" if origin == "-" else (origin or c.origin_host)
    m.screen("Модули → Настройки CDN")
    m.progress("Применение настроек...")
    try:
        m.show_result(svc.set_cdn(domain, origin), "Настройки CDN применены.")
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
    m.ui.pause()


# ─── Caddy ───────────────────────────────────────────────────────────────────


def caddy_module_menu(m: Menu) -> None:
    mod = CaddyModule(m.app)
    while True:
        options = ["Перезапустить службу", "Просмотреть логи службы", "Обновить Caddy", "Назад"]
        choice = m.select("Модуль: Caddy", options)
        if choice in (BACK, 3):
            return
        if choice == 0:
            _service_action(
                m,
                "Модули → Caddy → Перезапуск",
                "Перезапуск службы Caddy...",
                "Служба Caddy перезапущена.",
                mod.restart,
            )
        elif choice == 1:
            _logs(m, "Модули → Caddy → Логи службы", "caddy")
        else:
            update_caddy_menu(m, mod)


def update_caddy_menu(m: Menu, mod: CaddyModule) -> None:
    title = "Модули → Caddy → Обновление"
    m.screen(title)
    m.progress("Получение информации о версиях...")
    try:
        latest = mod.latest_version()
    except ServiceError:
        m.ui.out()
        m.fail("Ошибка: Не удалось получить последнюю версию с GitHub.")
        m.ui.pause()
        return
    pad = m.ui.block_pad()
    m.ui.out()
    m.ui.out(f"{pad}Caddy собирается вместе с NaiveProxy — это занимает до 5 минут.")
    m.ui.out(f"{pad}Прокси работают всё это время; Caddy заменяется в самом конце.")
    if not _confirm_versions(m, mod.info().version, latest):
        return
    m.ui.clear_screen()
    m.ui.t.cursor(True)
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.run(mod.rebuild_command(latest), check=False)
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
        # «Отключить» только выключает Mieru (служба и правила фаервола), пакет и
        # настройки остаются — «Включить» возвращает как было
        options = ["Отключить Mieru", "Перезапустить службу"] if s.enabled else ["Включить Mieru"]
        port_item = f"Изменить порт (Текущий порт: {s.port})"
        options += [port_item, "Обновить Mieru", "Назад"]
        choice = m.select("Модуль: Mieru", options)
        if choice == BACK or options[choice] == "Назад":
            return
        sel = options[choice]
        if sel == "Включить Mieru":
            m.screen("Модули → Mieru → Включение")
            m.progress("Включение Mieru...")
            m.show_result(mod.set_enabled(True), "Mieru включен. Сервисы перезапущены.")
            m.ui.pause()
        elif sel == "Отключить Mieru":
            m.screen("Модули → Mieru → Отключение")
            m.ui.out()
            m.ui.center_print(
                f"{YELLOW}Подключения Mieru у пользователей не будут работать до включения.{RESET}"
            )
            m.ui.center_print(
                f"{DIM}Mieru не удаляется: включить снова — этот же пункт меню.{RESET}"
            )
            m.ui.out()
            if m.ui.read_prompt("Отключить Mieru? (y/N): ") in ("y", "Y", "д", "Д"):
                m.progress("Отключение Mieru...")
                m.show_result(mod.set_enabled(False), "Mieru отключен. Сервисы перезапущены.")
            else:
                m.ui.out()
                m.ui.center_print("Отключение отменено.")
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
            update_core_menu(m, "Модули → Mieru → Обновление", mod)


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


# (ядро, поле, подпись, транспорт) — порядок строк в меню путей
_PATH_ROWS: list[tuple[str, str, str, Transport]] = [
    ("vless", "ws_path", "sing-box  Путь WS:          ", Transport.WS),
    ("vless", "grpc_service", "sing-box  Сервис gRPC:      ", Transport.GRPC),
    ("vless", "httpupgrade_path", "sing-box  Путь HTTPUpgrade: ", Transport.HTTPUPGRADE),
    ("xray", "ws_path", "Xray      Путь WS:          ", Transport.WS),
    ("xray", "grpc_service", "Xray      Сервис gRPC:      ", Transport.GRPC),
    ("xray", "httpupgrade_path", "Xray      Путь HTTPUpgrade: ", Transport.HTTPUPGRADE),
    ("xray", "xhttp_path", "Xray      Путь XHTTP:       ", Transport.XHTTP),
    ("xray", "cdn_path", "Xray      Путь CDN:         ", Transport.CDN),
]


def change_vless_paths_menu(m: Menu) -> None:
    """Пути у каждого ядра свои (этап 11); строки Xray — если он установлен.
    Смена пути меняет ссылки у подключений этого ядра — меню предупреждает."""
    svc = SettingsService(m.app)
    while True:
        s = svc.get()
        rows = [
            r
            for r in _PATH_ROWS
            if r[0] == "vless" or (s.xray.configured and (r[1] != "cdn_path" or s.xray.cdn_path))
        ]
        options = [
            f"{label}{getattr(getattr(s, core), field) or '(пусто)'}"
            for core, field, label, _ in rows
        ]
        options.append("Назад")
        choice = m.select("Пути протоколов", options)
        if choice == BACK or choice == len(rows):
            return
        core, field, _, transport = rows[choice]
        kind = CoreKind.XRAY if core == "xray" else CoreKind.SINGBOX
        affected = sum(
            1
            for u in m.app.users.all()
            for c in u.connections
            if c.core is kind and c.transport is transport
        )
        m.screen("Модули → Пути протоколов")
        m.ui.out()
        if affected:
            m.ui.center_print(
                f"{YELLOW}⚠ Ссылки изменятся у подключений: {affected}. "
                f"Клиентам нужно обновить подписку.{RESET}"
            )
            m.ui.out()
        is_service = field == "grpc_service"
        prompt = (
            "Введите имя gRPC сервиса: "
            if is_service
            else "Введите путь (должен начинаться со слэша /): "
        )
        value = m.ui.read_prompt(prompt).replace(" ", "")
        if not value:
            continue
        if not is_service and not value.startswith("/"):
            m.ui.out()
            m.fail("Некорректный путь (должен начинаться со слэша /)")
            m.ui.pause()
            continue
        m.screen("Модули → Пути протоколов")
        m.progress("Применение настроек...")
        try:
            res = svc.set_path(core, field, value)
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
