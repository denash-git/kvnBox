"""Шапка экрана — порт print_header / refresh_system_status из Bash-меню."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kvnbox import __version__
from kvnbox.services.app import App
from kvnbox.services.status import StatusService
from kvnbox.tui.term import BLUE, BOLD, GREEN, RED, RESET, YELLOW
from kvnbox.tui.widgets import BLOCK, Ui

MAIN_TITLE = f"kvnBox v. {__version__}"
LABEL_WIDTH = 14  # «Домен:        » — подписи выровнены по 14 символам


def service_status(state: str) -> tuple[str, str]:
    """get_service_status: (цветной текст, текст без цвета)."""
    if state == "active":
        return f"{GREEN}● работает{RESET}", "● работает"
    return f"{RED}● не запущен ({state}){RESET}", f"● не запущен ({state})"


def _on_off(on: bool, yes: str, no: str) -> tuple[str, str]:
    return (f"{GREEN}● {yes}{RESET}", f"● {yes}") if on else (f"{RED}● {no}{RESET}", f"● {no}")


def netbird_status(state: str, dot: bool = True) -> str:
    """get_netbird_status: состояние подключения NetBird для шапки и экрана модуля."""
    d = "● " if dot else ""
    return {
        "not_installed": f"{RED}{d}не установлен{RESET}",
        "stopped": f"{RED}{d}остановлен (служба){RESET}",
        "needs_login": f"{YELLOW}{d}требует авторизации{RESET}",
        "connected": f"{GREEN}{d}подключен{RESET}",
    }.get(state, f"{RED}{d}отключен{RESET}")


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


@dataclass
class HeaderStatus:
    domain: str = "не задан"
    fqdn: str = ""
    rows: list[tuple[str, str, str]] | None = None  # (подпись, цветной текст, без цвета)
    update_badge: bool = False


def collect(app: App) -> HeaderStatus:
    """refresh_system_status: собирается при каждом возврате в главное меню."""
    from kvnbox.services.modules import NetbirdModule

    st = StatusService(app)
    try:
        domain = app.settings.load().domain
    except Exception:
        domain = "не задан"
    fqdn = app.runner.run(["hostname", "-f"], timeout=10).stdout.strip()
    if not fqdn:
        fqdn = app.runner.run(["hostname"], timeout=10).stdout.strip()
    sd = app.systemd
    rows = [
        ("TCP BBR:", *_on_off(app.bbr.is_active(), "активен", "отключен")),
        ("Caddy:", *service_status(sd.state("caddy"))),
        ("sing-box:", *service_status(sd.state("sing-box"))),
        (
            "Mieru:",
            *_on_off(app.installed("mita") and sd.is_active("mita"), "работает", "не запущен"),
        ),
    ]
    if app.installed("netbird"):
        text = netbird_status(NetbirdModule(app).connection_state())
        rows.append(("NetBird:", text, re.sub(r"\x1b\[[0-9;]*m", "", text)))
    rows += [
        ("SSH пароль:", *_on_off(app.sshd.auth_mode() == "password", "включен", "отключен")),
        ("Защита:", *_on_off(app.fail2ban.is_active(), "активна", "отключена")),
    ]
    latest = st.update_cache()
    badge = bool(latest) and _version_key(latest) > _version_key(__version__)
    return HeaderStatus(domain=domain, fqdn=fqdn, rows=rows, update_badge=badge)


def print_header(ui: Ui, title: str, status: HeaderStatus | None, show_status: bool) -> None:
    if title == MAIN_TITLE and status and status.update_badge:
        title = f"{title} {GREEN}[↑ update]{RESET}"
    ui.center_print(f"{BLUE}{'═' * BLOCK}{RESET}")
    ui.center_print(f"{YELLOW}{title}{RESET}")
    pad = ""
    if show_status and status is not None:
        rows = status.rows or []
        lengths = [LABEL_WIDTH + len(status.domain), LABEL_WIDTH + len(status.fqdn)]
        lengths += [LABEL_WIDTH + len(raw) for _, _, raw in rows]
        indent = max((ui.width - max(lengths)) // 2, 0)
        pad = " " * indent
        ui.out()
        ui.out(f"{pad}{'Домен:':<{LABEL_WIDTH}}{BOLD}{status.domain}{RESET}")
        ui.out(f"{pad}{'FQDN VPS:':<{LABEL_WIDTH}}{BOLD}{status.fqdn}{RESET}")
    ui.center_print(f"{BLUE}{'─' * BLOCK}{RESET}")
    if show_status and status is not None:
        for label, text, _ in status.rows or []:
            ui.out(f"{pad}{label:<{LABEL_WIDTH}}{text}")
        ui.center_print(f"{BLUE}{'═' * BLOCK}{RESET}")
    ui.out()
