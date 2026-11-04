"""Разбор аргументов и выполнение команд. Вся логика — в сервисах."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from kvnbox import __version__
from kvnbox.core.models import (
    Connection,
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UsersDB,
    UuidCredentials,
    new_password,
)
from kvnbox.core.storage import StorageError
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult
from kvnbox.services.backup import BackupService, PasswordRequired
from kvnbox.services.jobs import Jobs
from kvnbox.services.modules import BbrModule, MieruModule, SingboxModule
from kvnbox.services.schedule import write_cron
from kvnbox.services.security import SecurityService
from kvnbox.services.ssh import SshService
from kvnbox.services.status import StatusService
from kvnbox.services.users import UserService, slot_label

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

SLOTS: dict[str, tuple[Protocol, Transport | None]] = {
    "vless-ws": (Protocol.VLESS, Transport.WS),
    "vless-grpc": (Protocol.VLESS, Transport.GRPC),
    "vless-xhttp": (Protocol.VLESS, Transport.HTTPUPGRADE),
    "naive": (Protocol.NAIVE, None),
    "mieru": (Protocol.MIERU, None),
}

Handler = Callable[[App, argparse.Namespace, TextIO], int]


# ─── вспомогательное ─────────────────────────────────────────────────────────


def _result(res: ApplyResult, out: TextIO) -> int:
    print(("✓ " if res.ok else "✗ ") + res.message, file=out)
    for line in res.log:
        print(f"  - {line}", file=out)
    return EXIT_OK if res.ok else EXIT_FAIL


def _slot_name(conn: Connection) -> str:
    return next(name for name, slot in SLOTS.items() if slot == conn.slot)


# ─── init / apply / status ───────────────────────────────────────────────────


def cmd_init(app: App, a: argparse.Namespace, out: TextIO) -> int:
    """Первичные данные, как делает установщик: настройки + пользователь admin
    (NaiveProxy с логином admin_naive и VLESS over WebSocket)."""
    if app.settings.store.exists() and not a.force:
        print("✗ Панель уже инициализирована (используйте --force для перезаписи).", file=out)
        return EXIT_FAIL
    fqdn = app.runner.run(["hostname", "-f"], timeout=10).stdout.strip()
    settings = Settings(
        domain=a.domain, admin_email=a.email or "", bbr_enabled=not a.no_bbr, fqdn=fqdn
    )
    admin = User(
        name="admin",
        connections=[
            Connection(
                protocol=Protocol.NAIVE,
                core=CoreKind.CADDY,
                credentials=PasswordCredentials(username="admin_naive", password=new_password()),
            ),
            Connection(
                protocol=Protocol.VLESS,
                transport=Transport.WS,
                core=CoreKind.SINGBOX,
                credentials=UuidCredentials(),
            ),
        ],
    )
    app.settings.save(settings)
    app.users.store.save(UsersDB(users=[admin]))
    write_cron(settings, app.paths)
    print(f"✓ Инициализировано: домен {settings.domain}, пользователь admin.", file=out)
    return EXIT_OK


def cmd_apply(app: App, a: argparse.Namespace, out: TextIO) -> int:
    return _result(Applier(app).apply(), out)


def cmd_status(app: App, a: argparse.Namespace, out: TextIO) -> int:
    ov = StatusService(app).overview()
    if a.json:
        print(json.dumps(asdict(ov), ensure_ascii=False, indent=2), file=out)
        return EXIT_OK
    print(
        f"kvnBox {ov.version}"
        + (f" (доступна {ov.latest_version})" if ov.update_available else ""),
        file=out,
    )
    print(f"Домен: {ov.domain}   FQDN: {ov.fqdn}", file=out)
    for unit, state in ov.services.items():
        print(f"  {unit:9} {state}", file=out)
    print(
        f"BBR: {'вкл' if ov.bbr else 'выкл'}   Защита: {'вкл' if ov.security else 'выкл'}", file=out
    )
    return EXIT_OK


# ─── пользователи ────────────────────────────────────────────────────────────


def cmd_user_list(app: App, a: argparse.Namespace, out: TextIO) -> int:
    users = UserService(app).all()
    if not users:
        print("Пользователей нет.", file=out)
    for u in users:
        mark = "🟢" if u.active else "🔴"
        slots = ", ".join(_slot_name(c) + ("" if c.enabled else " (выкл)") for c in u.connections)
        print(f"{mark} {u.name}: {slots}", file=out)
    return EXIT_OK


def cmd_user_add(app: App, a: argparse.Namespace, out: TextIO) -> int:
    protocol, transport = SLOTS[a.protocol]
    svc = UserService(app)
    conn, res = svc.add_connection(a.name, protocol, transport)
    code = _result(res, out)
    if res.ok:
        print(f"{slot_label(conn)}: {svc.link(svc.get(a.name), conn)}", file=out)
    return code


def cmd_user_remove(app: App, a: argparse.Namespace, out: TextIO) -> int:
    svc = UserService(app)
    if a.protocol:
        return _result(svc.remove_connection(a.name, *SLOTS[a.protocol]), out)
    return _result(svc.delete(a.name), out)


def _toggle(enabled: bool) -> Handler:
    def handler(app: App, a: argparse.Namespace, out: TextIO) -> int:
        slot = SLOTS[a.protocol] if a.protocol else (None, None)
        return _result(UserService(app).set_enabled(a.name, enabled, *slot), out)

    return handler


def cmd_user_links(app: App, a: argparse.Namespace, out: TextIO) -> int:
    svc = UserService(app)
    user = svc.get(a.name)
    print(f"Подписка: {svc.subscription_url(user)}", file=out)
    for c in user.connections:
        state = "" if c.enabled else " [выключено]"
        print(f"{slot_label(c)}{state}:\n  {svc.link(user, c)}", file=out)
    return EXIT_OK


# ─── бэкап ───────────────────────────────────────────────────────────────────


def cmd_backup_create(app: App, a: argparse.Namespace, out: TextIO) -> int:
    password = app.settings.load().backup.password if a.encrypt else None
    if a.encrypt and not password:
        print("✗ Пароль для бэкапов не задан.", file=out)
        return EXIT_FAIL
    print(f"✓ {BackupService(app).create(password=password)}", file=out)
    return EXIT_OK


def cmd_backup_list(app: App, a: argparse.Namespace, out: TextIO) -> int:
    for p in BackupService(app).all():
        print(p.name, file=out)
    return EXIT_OK


def cmd_backup_restore(app: App, a: argparse.Namespace, out: TextIO) -> int:
    svc = BackupService(app)
    path = Path(a.path) if a.path else svc.latest()
    if path is None:
        print("✗ Бэкапов нет.", file=out)
        return EXIT_FAIL
    res = svc.restore(path, a.password)
    print(("✓ " if res.ok else "✗ ") + res.message, file=out)
    for line in res.log:
        print(f"  - {line}", file=out)
    return EXIT_OK if res.ok else EXIT_FAIL


# ─── защита, SSH, модули ─────────────────────────────────────────────────────


def cmd_security_status(app: App, a: argparse.Namespace, out: TextIO) -> int:
    st = SecurityService(app).status()
    print(
        f"Защита: {'активна' if st.active else 'выключена'}; "
        f"порт 80: {'открыт' if st.port80_allowed else 'закрыт'}",
        file=out,
    )
    for j in st.jails:
        print(f"  {j.name}: заблокировано {j.currently_banned}", file=out)
    for ip in st.banned_ips:
        print(f"    {ip}", file=out)
    return EXIT_OK


def _say(fn: Callable[[App, argparse.Namespace], str]) -> Handler:
    def handler(app: App, a: argparse.Namespace, out: TextIO) -> int:
        print(f"✓ {fn(app, a)}", file=out)
        return EXIT_OK

    return handler


def cmd_ssh_status(app: App, a: argparse.Namespace, out: TextIO) -> int:
    st = SshService(app).status()
    print(
        f"Порт: {st.port}; вход по паролю: {'включен' if st.password_allowed else 'отключен'}",
        file=out,
    )
    for k in st.keys:
        print(f"  #{k.index} {k.type} {k.fingerprint} {k.comment}", file=out)
    return EXIT_OK


def cmd_ssh_port(app: App, a: argparse.Namespace, out: TextIO) -> int:
    old = SshService(app).change_port(a.port)
    print(
        f"✓ SSH переведен на порт {a.port}. Проверьте вход по новому порту в отдельном окне, "
        f"затем выполните: kvnbox ssh confirm-port {old}",
        file=out,
    )
    return EXIT_OK


def cmd_job_check_services(app: App, a: argparse.Namespace, out: TextIO) -> int:
    for m in Jobs(app).check_services():
        print(m, file=out)
    return EXIT_OK


def cmd_job_check_updates(app: App, a: argparse.Namespace, out: TextIO) -> int:
    latest = Jobs(app).check_updates()
    if latest:
        print(f"Доступна версия {latest}", file=out)
    return EXIT_OK


def cmd_menu(app: App, a: argparse.Namespace, out: TextIO) -> int:
    from kvnbox.tui.menu import Menu

    return Menu(app).run()


# ─── парсер ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kvnbox", description="kvnBox — панель прокси-сервера")
    p.add_argument("-V", "--version", action="version", version=f"kvnBox {__version__}")
    sub = p.add_subparsers(dest="command", metavar="КОМАНДА")

    def cmd(
        parent: argparse._SubParsersAction[argparse.ArgumentParser],
        name: str,
        handler: Handler,
        help: str,
    ) -> argparse.ArgumentParser:
        sp = parent.add_parser(name, help=help, description=help)
        sp.set_defaults(handler=handler)
        return sp

    def group(name: str, help: str) -> argparse._SubParsersAction[argparse.ArgumentParser]:
        gp = sub.add_parser(name, help=help, description=help)
        gp.set_defaults(group_parser=gp)
        return gp.add_subparsers(dest="action", metavar="ДЕЙСТВИЕ")

    sp = cmd(sub, "init", cmd_init, "первичная настройка (используется установщиком)")
    sp.add_argument("--domain", required=True)
    sp.add_argument("--email", default="")
    sp.add_argument("--no-bbr", action="store_true")
    sp.add_argument("--force", action="store_true")
    cmd(sub, "apply", cmd_apply, "пересоздать конфигурации и перезапустить службы")
    sp = cmd(sub, "status", cmd_status, "состояние панели и служб")
    sp.add_argument("--json", action="store_true")
    cmd(sub, "menu", cmd_menu, "интерактивное меню")

    users = group("user", "пользователи")
    cmd(users, "list", cmd_user_list, "список пользователей")
    sp = cmd(users, "add", cmd_user_add, "добавить подключение (пользователь создаётся)")
    sp.add_argument("name")
    sp.add_argument("protocol", choices=list(SLOTS))
    sp = cmd(users, "remove", cmd_user_remove, "удалить пользователя или одно подключение")
    sp.add_argument("name")
    sp.add_argument("protocol", nargs="?", choices=list(SLOTS))
    for name, enabled in (("enable", True), ("disable", False)):
        sp = cmd(users, name, _toggle(enabled), "включить" if enabled else "выключить")
        sp.add_argument("name")
        sp.add_argument("protocol", nargs="?", choices=list(SLOTS))
    sp = cmd(users, "links", cmd_user_links, "ссылки и подписка пользователя")
    sp.add_argument("name")

    backup = group("backup", "резервные копии")
    sp = cmd(backup, "create", cmd_backup_create, "создать бэкап")
    sp.add_argument("--encrypt", action="store_true", help="зашифровать паролем из настроек")
    cmd(backup, "list", cmd_backup_list, "список бэкапов")
    sp = cmd(backup, "restore", cmd_backup_restore, "восстановить (по умолчанию — последний)")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--password")

    sec = group("security", "защита от сканеров")
    cmd(sec, "status", cmd_security_status, "состояние и заблокированные IP")
    cmd(sec, "enable", _say(lambda app, a: SecurityService(app).enable()), "включить")
    cmd(sec, "disable", _say(lambda app, a: SecurityService(app).disable()), "выключить")
    sp = cmd(
        sec, "unban", _say(lambda app, a: SecurityService(app).unban(a.ip)), "разблокировать IP"
    )
    sp.add_argument("ip")
    cmd(
        sec,
        "unban-all",
        _say(lambda app, a: SecurityService(app).unban_all()),
        "разблокировать всех",
    )

    ssh = group("ssh", "доступ по SSH")
    cmd(ssh, "status", cmd_ssh_status, "порт, режим входа, ключи")
    sp = cmd(ssh, "port", cmd_ssh_port, "сменить порт SSH")
    sp.add_argument("port", type=int)

    def confirm(app: App, a: argparse.Namespace) -> str:
        SshService(app).confirm_port_change(a.old_port)
        return f"Правило для старого порта {a.old_port} убрано (кроме 22)."

    sp = cmd(ssh, "confirm-port", _say(confirm), "убрать правило старого порта")
    sp.add_argument("old_port", type=int)
    sp = cmd(
        ssh,
        "auth",
        _say(lambda app, a: SshService(app).set_auth_mode(a.mode)),
        "вход по паролю (password) или только по ключу (key)",
    )
    sp.add_argument("mode", choices=["password", "key"])

    mieru = group("mieru", "модуль Mieru")
    cmd(
        mieru, "install", lambda app, a, out: _result(MieruModule(app).install(), out), "установить"
    )
    cmd(
        mieru,
        "uninstall",
        lambda app, a, out: _result(MieruModule(app).uninstall(), out),
        "удалить",
    )
    cmd(
        mieru,
        "enable",
        lambda app, a, out: _result(MieruModule(app).set_enabled(True), out),
        "включить",
    )
    cmd(
        mieru,
        "disable",
        lambda app, a, out: _result(MieruModule(app).set_enabled(False), out),
        "выключить",
    )
    sp = cmd(
        mieru,
        "port",
        lambda app, a, out: _result(MieruModule(app).set_port(a.port), out),
        "сменить порт",
    )
    sp.add_argument("port", type=int)

    singbox = group("singbox", "модуль sing-box")
    cmd(
        singbox,
        "update",
        lambda app, a, out: _result(SingboxModule(app).update(), out),
        "обновить до последней версии",
    )

    bbr = group("bbr", "TCP BBR")
    cmd(bbr, "toggle", _say(lambda app, a: BbrModule(app).toggle()), "включить/выключить")

    job = group("job", "задачи по расписанию (cron)")
    cmd(job, "check-services", cmd_job_check_services, "проверить службы и ресурсы")
    cmd(job, "check-updates", cmd_job_check_updates, "проверить обновления")
    sp = cmd(
        job,
        "backup",
        _say(lambda app, a: Jobs(app).backup(ignore_enabled=a.now)),
        "отправить бэкап в Telegram",
    )
    sp.add_argument("--now", action="store_true", help="даже если автобэкап выключен")
    return p


# ─── точка входа ─────────────────────────────────────────────────────────────


def menu_main() -> int:
    """Команда `menu` — как в TransferBox."""
    return main(["menu"])


def main(argv: list[str] | None = None, app: App | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:  # --help, --version, ошибка аргументов
        return int(e.code or 0)
    handler: Handler | None = getattr(a, "handler", None)
    if handler is None:
        (getattr(a, "group_parser", None) or parser).print_help(out)
        return EXIT_USAGE
    if app is None:
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            print("✗ Требуются права root: sudo kvnbox …", file=sys.stderr)
            return EXIT_FAIL
        app = App.create()
    try:
        return handler(app, a, out)
    except PasswordRequired as e:
        print(f"✗ {e} Укажите --password.", file=out)
        return EXIT_FAIL
    except (ServiceError, StorageError) as e:
        print(f"✗ {e}", file=out)
        return EXIT_FAIL
    except ValidationError as e:
        msg = str(e.errors()[0].get("msg", e)).removeprefix("Value error, ")
        print(f"✗ {msg}", file=out)
        return EXIT_FAIL
