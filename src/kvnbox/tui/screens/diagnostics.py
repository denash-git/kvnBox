"""Раздел «Диагностика» — порт diagnostics_menu, logs_menu, system_monitor_menu,
run_speedtest_vps_internet, run_speedtest_client_vps."""

from __future__ import annotations

import contextlib
import re
import subprocess

from kvnbox.services import ServiceError
from kvnbox.services.diagnostics import DiagnosticsService
from kvnbox.system.metrics import cpu_percent
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import BLUE, BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW
from kvnbox.tui.widgets import BACK, BLOCK

LOG_LINES = 100
LOG_UNITS = [
    ("Caddy", "caddy", None),
    ("sing-box", "sing-box", None),
    ("Mieru", "mita", "mita"),
    ("NetBird", "netbird", "netbird"),
]


def diagnostics_menu(m: Menu) -> None:
    while True:
        choice = m.select(
            "Диагностика",
            [
                "Мониторинг ресурсов",
                "Показать логи",
                "Тест VPS → Интернет (speedtest-cli)",
                "Замер скорости Client ↔ VPS (iperf3)",
                "Назад",
            ],
        )
        if choice in (BACK, 4):
            return
        [system_monitor_menu, logs_menu, run_speedtest_vps_internet, run_speedtest_client_vps][
            choice
        ](m)


# ─── логи ────────────────────────────────────────────────────────────────────


def _follow(m: Menu, argv: list[str]) -> None:
    """`journalctl -f` в текущем терминале; Ctrl+C возвращает в меню."""
    with contextlib.suppress(KeyboardInterrupt):
        subprocess.run(argv, check=False)


def logs_menu(m: Menu) -> None:
    while True:
        units = [
            (name, unit)
            for name, unit, binary in LOG_UNITS
            if binary is None or m.app.installed(binary)
        ]
        options = [f"Последние {LOG_LINES} строк — {name}" for name, _ in units]
        options += [f"Следить за логом {name} (Ctrl+C — выход)" for name, _ in units]
        options.append("Назад")
        choice = m.select("Просмотр логов", options)
        if choice == BACK or options[choice] == "Назад":
            return
        name, unit = units[choice % len(units)]
        if choice < len(units):
            m.screen(f"Логи — {name}")
            res = m.app.runner.run(
                ["journalctl", "-u", unit, "-n", str(LOG_LINES), "--no-pager"], timeout=30
            )
            m.ui.t.write(res.stdout + res.stderr)
            m.ui.pause()
        else:
            m.screen(f"Следить за логом — {name}")
            m.ui.out(f"{m.ui.block_pad()}Нажмите Ctrl+C для выхода...\n")
            _follow(m, ["journalctl", "-u", unit, "-f"])


# ─── мониторинг ──────────────────────────────────────────────────────────────


def _bar(pct: int) -> str:
    filled = max(0, min(pct, 100)) * 20 // 100
    return "█" * filled + "░" * (20 - filled)


def _color(pct: int) -> str:
    return RED if pct >= 85 else YELLOW if pct >= 60 else GREEN


def _speed(kb: int) -> str:
    if kb > 1024:
        return f"{int(kb * 10 / 1024) / 10:.1f} MB/s"  # как `bc scale=1` — с отбрасыванием
    return f"{kb} KB/s"


def _cmd(m: Menu, *argv: str) -> str:
    return m.app.runner.run(list(argv), timeout=10).stdout


def system_monitor_menu(m: Menu) -> None:
    metrics = m.app.metrics
    iface = metrics.default_iface()
    m.ui.t.cursor(False)
    m.ui.clear_screen()
    while True:
        cpu1, net1 = metrics.cpu_times(), metrics.net(iface)
        key = m.ui.t.wait_input(1.0)
        if key is not None and key in ("q", "Q", "0", "ENTER"):
            m.ui.t.cursor(True)
            return
        cpu2, net2 = metrics.cpu_times(), metrics.net(iface)
        cpu = cpu_percent(cpu1, cpu2)
        mem = _cmd(m, "free", "-m").splitlines() or [""]
        mem_row = next((r.split() for r in mem if r.startswith("Mem:")), [])
        total, used, free, cache = (
            int(mem_row[i]) if len(mem_row) > i else 0 for i in (1, 2, 3, 5)
        )
        ram = used * 100 // total if total else 0
        dfh = ([*_cmd(m, "df", "-h", "/").splitlines(), "", ""])[1].split()
        dfp = ([*_cmd(m, "df", "/").splitlines(), "", ""])[1].split()
        disk_total, disk_used = (dfh[1], dfh[2]) if len(dfh) > 2 else ("?", "?")
        disk = int(dfp[4].rstrip("%")) if len(dfp) > 4 and dfp[4].rstrip("%").isdigit() else 0
        rx_kb = (net2.rx_bytes - net1.rx_bytes) // 1024 if net1 and net2 else 0
        tx_kb = (net2.tx_bytes - net1.tx_bytes) // 1024 if net1 and net2 else 0
        uptime = re.sub(r"^up ", "", _cmd(m, "uptime", "-p").strip())
        load = " ".join(f"{x:.2f}" for x in metrics.load_avg())

        w = m.ui.t.write
        m.ui.t.write("\033[H")
        m.header("Мониторинг ресурсов")
        m.ui.out()
        indent = max((m.ui.width - BLOCK) // 2, 2)
        c1, c2 = indent + 1, indent + 30
        sep = f"{DIM}{'─' * BLOCK}{RESET}"
        cc, rc, dc = _color(cpu), _color(ram), _color(disk)
        w(f"\033[{c1}G{BOLD}{BLUE}▸ CPU{RESET}\033[{c2}G{BOLD}{BLUE}▸ RAM{RESET}\n")
        w(
            f"\033[{c1}G{_bar(cpu)} {cc}{BOLD}{cpu:3d}%{RESET}"
            f"\033[{c2}G{_bar(ram)} {rc}{BOLD}{ram:3d}%{RESET}\n"
        )
        w(
            f"\033[{c1}G{DIM}Load avg:{RESET} {CYAN}{load}{RESET}"
            f"\033[{c2}G{CYAN}{used}{RESET}/{total} MB  св:{GREEN}{free}{RESET} "
            f"кэш:{DIM}{cache}{RESET} MB\n"
        )
        m.ui.out()
        m.ui.center_print(sep)
        m.ui.out()
        w(
            f"\033[{c1}G{BOLD}{BLUE}▸ DISK (/){RESET}\033[{c2}G{BOLD}{BLUE}▸ NETWORK{RESET}  "
            f"{DIM}({iface}){RESET}\n"
        )
        w(
            f"\033[{c1}G{_bar(disk)} {dc}{BOLD}{disk:3d}%{RESET}"
            f"\033[{c2}G{DIM}↓ RX:{RESET} {GREEN}{BOLD}{_speed(rx_kb):<10}{RESET}  "
            f"{DIM}↑ TX:{RESET} {YELLOW}{BOLD}{_speed(tx_kb)}{RESET}\n"
        )
        w(
            f"\033[{c1}G{DIM}Использовано:{RESET} {CYAN}{disk_used}{RESET} {DIM}из{RESET} "
            f"{disk_total}\n"
        )
        m.ui.out()
        m.ui.center_print(sep)
        m.ui.center_print(f"{DIM}Аптайм: {RESET}{CYAN}{uptime}{RESET}")
        m.ui.out()
        m.ui.center_print(sep)
        m.ui.center_print(f"{DIM}[Q / Enter — вернуться в меню]{RESET}")
        w("\033[J")


# ─── speedtest и iperf3 ──────────────────────────────────────────────────────


def run_speedtest_vps_internet(m: Menu) -> None:
    diag = DiagnosticsService(m.app)
    if not m.app.installed("speedtest"):
        m.screen("Скорость VPS → Интернет")
        m.ui.out()
        m.fail("Не удалось установить speedtest.")
        m.ui.pause()
        return
    while True:
        choice = m.select(
            "VPS → Интернет",
            [
                "Тест на ближайшем сервере (автовыбор)",
                "Выбрать сервер из списка ближайших",
                "Назад",
            ],
        )
        if choice == 0:
            m.screen("Скорость VPS → Интернет (Автовыбор)")
            m.ui.out(f"  {CYAN}Запуск теста, подождите...{RESET}\n")
            m.ui.out("\n".join("  " + line for line in diag.speedtest().splitlines()))
            m.ui.out()
            m.ui.pause()
            return
        if choice != 1:
            return
        m.screen("Поиск ближайших серверов")
        m.progress("Получение списка серверов...")
        try:
            servers = diag.speedtest_servers()
        except ServiceError:
            servers = []
        if not servers:
            m.ui.out()
            m.fail("Не удалось получить список серверов.")
            m.ui.pause()
            continue
        labels = [" ".join(f"[{s.id}] {s.name} {s.location} {s.country}".split()) for s in servers]
        sel = m.select("Выберите сервер", [*labels, "Назад"])
        if sel in (BACK, len(labels)):
            continue
        server = servers[sel]
        m.screen(f"VPS → Интернет ({labels[sel]})")
        m.ui.out(f"  {CYAN}Запуск теста на сервере {server.id}, подождите...{RESET}\n")
        m.ui.out("\n".join("  " + line for line in diag.speedtest(server.id).splitlines()))
        m.ui.out()
        m.ui.pause()
        return


def run_speedtest_client_vps(m: Menu) -> None:
    diag = DiagnosticsService(m.app)
    m.screen("Замер скорости: Client ↔ VPS (iperf3)")
    if not m.app.installed("iperf3"):
        m.ui.out()
        m.fail("Не удалось установить iperf3. Установите вручную: apt install iperf3")
        m.ui.pause()
        return
    ip = diag.public_ip() or "ВАШ_IP_VPS"
    try:
        diag.iperf_start()
    except ServiceError as e:
        m.ui.out()
        m.fail(str(e))
        m.ui.pause()
        return
    try:
        m.ui.out()
        m.ui.draw_line("─", BLUE)
        m.ui.out()
        m.ui.center_print(f"{GREEN}● Сервер iperf3 запущен и ожидает подключения{RESET}")
        m.ui.out()
        m.ui.center_print("Скачивание (клиент → VPS):")
        m.ui.center_print(f"{CYAN}iperf3 -c {ip}{RESET}")
        m.ui.out()
        m.ui.center_print("Отдача (VPS → клиент):")
        m.ui.center_print(f"{CYAN}iperf3 -c {ip} -R{RESET}")
        m.ui.out()
        m.ui.center_print(
            f"Телефон: {YELLOW}Magic iPerf{RESET} (Android) / "
            f"{YELLOW}HE.NET Network Tools{RESET} (iOS)"
        )
        m.ui.center_print(f"Адрес сервера: {CYAN}{ip}{RESET}")
        m.ui.out()
        m.ui.center_print(
            f"{DIM}Нажмите Enter или Ctrl+C для выхода из теста и возврата в меню.{RESET}"
        )
        m.ui.out()
        m.ui.draw_line("─", BLUE)
        m.ui.out()
        with contextlib.suppress(KeyboardInterrupt):
            m.ui.t.read_line()
    finally:
        diag.iperf_stop()
    m.ui.out()
    m.ok("Тестирование завершено.")
    m.ui.pause()
