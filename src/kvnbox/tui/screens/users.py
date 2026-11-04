"""Раздел «Пользователи» — порт users_menu, list_users_menu, user_management_menu,
protocol_management_menu, add_protocol_to_user, show_subscription_qr, create_user_menu."""

from __future__ import annotations

import time

from kvnbox.core.models import Connection, CoreKind, Protocol, Transport, User, supported_cores
from kvnbox.services import ServiceError
from kvnbox.services.users import MANAGE_LABELS, SHORT_LABELS, UserService, core_suffix
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import CYAN, DIM, GREEN, RED, RESET, YELLOW
from kvnbox.tui.widgets import BACK

ADD_OPTIONS: list[tuple[str, Protocol, Transport | None]] = [
    ("VLESS over WebSocket", Protocol.VLESS, Transport.WS),
    ("VLESS over gRPC", Protocol.VLESS, Transport.GRPC),
    ("VLESS over HTTPUpgrade", Protocol.VLESS, Transport.HTTPUPGRADE),
    ("VLESS over XHTTP (только Xray)", Protocol.VLESS, Transport.XHTTP),
    ("NaiveProxy", Protocol.NAIVE, None),
    ("Mieru", Protocol.MIERU, None),
]
NO_QR = "  [qrencode не установлен на сервере]"


def _svc(m: Menu) -> UserService:
    return UserService(m.app)


def users_menu(m: Menu) -> None:
    while True:
        choice = m.select(
            "Пользователи", ["Список пользователей", "Добавить нового пользователя", "Назад"]
        )
        if choice == 0:
            list_users_menu(m)
        elif choice == 1:
            create_user_menu(m)
        elif choice in (2, BACK):
            return


def list_users_menu(m: Menu) -> None:
    while True:
        users = sorted(_svc(m).all(), key=lambda u: u.name)  # как jq group_by
        if not users:
            m.screen("Список пользователей")
            m.ui.out("  Пользователей пока нет.")
            m.ui.pause()
            return
        col = max(len(u.name) for u in users) + 4
        options = [
            f"{u.name}{' ' * (col - len(u.name))}(Протоколы: "
            + ", ".join(SHORT_LABELS[c.slot] for c in u.connections)
            + ")"
            for u in users
        ]
        choice = m.select("Список пользователей", [*options, "Назад"])
        if choice in (BACK, len(options)):
            return
        user_management_menu(m, users[choice].name)


def user_management_menu(m: Menu, nick: str) -> None:
    while True:
        user = m.app.users.get(nick)
        if user is None:
            return
        options = []
        for c in user.connections:
            status = f"{GREEN}активен{RESET}" if c.enabled else f"{RED}отключен{RESET}"
            options.append(f"• {MANAGE_LABELS[c.slot]}{core_suffix(c)} [{status}]")
        n = len(options)
        options += [
            "[+] Добавить протокол",
            "[i] Ссылка подписки & QR",
            "[x] Удалить пользователя полностью",
            "Назад",
        ]
        choice = m.select(f"Управление: {nick}", options)
        if choice in (BACK, len(options) - 1):
            return
        if choice < n:
            protocol_management_menu(m, nick, user.connections[choice])
        elif choice == n:
            add_protocol_to_user(m, nick)
        elif choice == n + 1:
            show_subscription_qr(m, nick)
        elif choice == n + 2:
            m.screen(f"Удаление пользователя {nick}")
            m.ui.out()
            answer = m.ui.read_prompt(
                f"Вы действительно хотите полностью удалить пользователя {nick} "
                "и все его протоколы? [y/N]: "
            )
            if answer in ("y", "Y"):
                m.progress("Удаление пользователя...")
                try:
                    m.show_result(_svc(m).delete(nick), f"Пользователь {nick} успешно удален!")
                except ServiceError as e:
                    m.ui.out()
                    m.fail(f"Ошибка при удалении: {e}")
                m.ui.pause()
                return


def protocol_management_menu(m: Menu, nick: str, conn: Connection) -> None:
    label = MANAGE_LABELS[conn.slot] + core_suffix(conn)
    protocol, transport = conn.slot
    while True:
        user = m.app.users.get(nick)
        current = user.find(protocol, transport) if user else None
        if user is None or current is None:
            return
        toggle = (
            f"Отключить протокол {GREEN}[активен]{RESET}"
            if current.enabled
            else f"Включить протокол {RED}[отключен]{RESET}"
        )
        choice = m.select(
            f"{nick} ({label})",
            [toggle, "Показать ссылку подключения и QR-код", "Удалить этот протокол", "Назад"],
        )
        if choice in (BACK, 3):
            return
        if choice == 0:
            m.screen(f"{nick} ({label}) → Изменение статуса")
            m.progress("Изменение статуса протокола...")
            enable = not current.enabled
            try:
                res = _svc(m).set_enabled(nick, enable, protocol, transport)
                m.show_result(
                    res, "Протокол успешно включен!" if enable else "Протокол успешно отключен."
                )
            except ServiceError as e:
                m.ui.out()
                m.fail(f"Ошибка: {e}")
            m.ui.pause()
        elif choice == 1:
            m.ui.clear_screen()
            m.header(f"Подключение: {nick} ({label})")
            _print_connection(m, user, current)
            m.ui.pause()
        elif choice == 2:
            m.screen(f"Удаление протокола: {label} ({nick})")
            m.ui.out()
            answer = m.ui.read_prompt(
                f"Вы действительно хотите удалить протокол {label} для {nick}? [y/N]: "
            )
            if answer in ("y", "Y"):
                m.progress("Удаление протокола...")
                try:
                    res = _svc(m).remove_connection(nick, protocol, transport)
                    m.show_result(res, f"Протокол {label} для {nick} успешно удален!")
                except ServiceError as e:
                    m.ui.out()
                    m.fail(f"Ошибка при удалении: {e}")
                m.ui.pause()
                return


def _qr(m: Menu, text: str) -> None:
    try:
        m.ui.t.write(_svc(m).qr_terminal(text))
    except ServiceError:
        m.ui.out(NO_QR)


def _print_connection(m: Menu, user: User, conn: Connection) -> None:
    svc = _svc(m)
    link = svc.link(user, conn)
    if conn.protocol is Protocol.MIERU:
        singbox_json = svc.mieru_configs(user, conn)["singbox"]
        m.ui.out("  1. Ссылка подключения (для Hiddify/mihomo):")
        m.ui.out(f"  {CYAN}{link}{RESET}\n")
        m.ui.out("  QR-код для ссылки подключения:")
        _qr(m, link)
        m.ui.out("\n  " + "─" * 60 + "\n")
        m.ui.out("  2. Sing-box JSON (для Karing):")
        m.ui.out(f"{GREEN}{singbox_json}{RESET}\n")
        m.ui.out("  QR-код для Sing-box JSON (сканировать в Karing):")
        _qr(m, singbox_json)
    else:
        m.ui.out("  Ссылка подключения:")
        m.ui.out(f"  {CYAN}{link}{RESET}")
        m.ui.out("\n  QR-код:")
        _qr(m, link)


def _offer_telegram(m: Menu, nick: str) -> None:
    tg = m.app.settings.load().telegram
    if not (tg.enabled and tg.token and tg.chat_id):
        return
    m.ui.out()
    answer = m.ui.read_prompt("Отправить ссылки и QR-коды в Telegram? [y/N]: ")
    if answer in ("y", "Y", "д", "Д"):
        m.ui.out()
        m.ui.center_print(f"{DIM}[*] Отправка сообщения в Telegram...{RESET}")
        try:
            _svc(m).send_to_telegram(nick)
            m.ok("Отправлено!")
        except ServiceError as e:
            m.fail(str(e))
        time.sleep(1)


def _available(m: Menu, user: User | None) -> list[tuple[str, Protocol, Transport | None]]:
    mieru_on = m.app.settings.load().mieru.enabled
    xray_ready = _svc(m).xray_ready()
    out = []
    for label, protocol, transport in ADD_OPTIONS:
        if protocol is Protocol.MIERU and not mieru_on:
            continue
        if transport is Transport.XHTTP and not xray_ready:
            continue
        if user is not None and user.find(protocol, transport) is not None:
            continue
        out.append((label, protocol, transport))
    return out


CORE_CANCEL = "cancel"


def _choose_core(
    m: Menu, nick: str, label: str, protocol: Protocol, transport: Transport | None
) -> CoreKind | str:
    """Шаг 2 добавления — ядро (этап 11). Спрашиваем, только если выбор есть:
    VLESS WS/gRPC/HTTPUpgrade и Xray установлен. Ядро потом не меняется (Q-010)."""
    cores = supported_cores(protocol, transport)
    if len(cores) == 1:
        return cores[0]
    if not _svc(m).xray_ready():
        return CoreKind.SINGBOX
    choice = m.select(f"Ядро для {nick}: {label}", ["sing-box (по умолчанию)", "Xray", "Отмена"])
    if choice in (BACK, 2):
        return CORE_CANCEL
    return CoreKind.SINGBOX if choice == 0 else CoreKind.XRAY


def add_protocol_to_user(m: Menu, nick: str) -> None:
    options = _available(m, m.app.users.get(nick))
    if not options:
        m.screen(f"Добавить протокол для {nick}")
        m.ui.out("  У этого пользователя уже добавлены все доступные протоколы.")
        m.ui.pause()
        return
    choice = m.select(f"Добавить протокол для {nick}", [o[0] for o in options] + ["Отмена"])
    if choice in (BACK, len(options)):
        return
    label, protocol, transport = options[choice]
    core = _choose_core(m, nick, label, protocol, transport)
    if not isinstance(core, CoreKind):
        return
    m.screen(f"Добавление протокола для {nick}")
    m.progress("Добавление протокола...")
    try:
        conn, res = _svc(m).add_connection(nick, protocol, transport, core)
    except ServiceError as e:
        _error(m, f"Ошибка добавления протокола: {e}")
        return
    if not res.ok:
        _error(m, f"Ошибка добавления протокола: {res.message}")
        return
    label = MANAGE_LABELS[conn.slot] + core_suffix(conn)
    m.ui.clear_screen()
    m.header(f"Протокол добавлен: {nick} ({label})")
    _print_connection(m, _svc(m).get(nick), conn)
    _offer_telegram(m, nick)
    m.ui.pause()


def _error(m: Menu, text: str) -> None:
    m.screen("Ошибка")
    m.ui.out()
    m.fail(text)
    m.ui.pause()


def _print_subscription(m: Menu, user: User) -> None:
    url = _svc(m).subscription_url(user)
    m.ui.out("  Ссылка подписки:")
    m.ui.out(f"  {CYAN}{url}{RESET}")
    m.ui.out("\n  QR-код:")
    _qr(m, url)


def show_subscription_qr(m: Menu, nick: str) -> None:
    user = m.app.users.get(nick)
    if user is None:
        _error(m, "Ошибка: Не удалось найти токен подписки для этого пользователя.")
        return
    m.screen(f"Подписка пользователя: {nick}")
    _print_subscription(m, user)
    m.ui.pause()


def create_user_menu(m: Menu) -> None:
    m.screen("Новый пользователь")
    nick = m.ui.read_prompt("Введите никнейм пользователя (или 0 для отмены): ").replace(" ", "")
    if not nick or nick == "0":
        return
    existing = m.app.users.get(nick)
    if existing is not None:
        m.ui.out(f"\n  {YELLOW}⚠ Пользователь с никнеймом '{nick}' уже существует.{RESET}")
        answer = m.ui.read_prompt("Хотите добавить ему дополнительный протокол? [y/N]: ")
        if answer not in ("y", "Y"):
            return
    options = _available(m, existing)
    choice = m.select(f"Протокол для {nick}", [o[0] for o in options] + ["Отмена"])
    if choice in (BACK, len(options)):
        return
    label, protocol, transport = options[choice]
    core = _choose_core(m, nick, label, protocol, transport)
    if not isinstance(core, CoreKind):
        return
    m.screen(f"Создание пользователя: {nick}")
    m.progress("Добавление пользователя...")
    try:
        conn, res = _svc(m).add_connection(nick, protocol, transport, core)
    except ServiceError as e:
        _error(m, f"Ошибка создания пользователя: {e}")
        return
    if not res.ok:
        _error(m, f"Ошибка создания пользователя: {res.message}")
        return
    # как при добавлении протокола: сразу ссылка и QR, затем Telegram
    label = MANAGE_LABELS[conn.slot] + core_suffix(conn)
    m.ui.clear_screen()
    m.header(f"Пользователь создан: {nick} ({label})")
    _print_connection(m, _svc(m).get(nick), conn)
    _offer_telegram(m, nick)
    m.ui.pause()
