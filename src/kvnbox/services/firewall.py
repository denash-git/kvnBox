"""Исходные правила фаервола при установке (как install.sh TransferBox)."""

from __future__ import annotations

import os

from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.ssh import SSH_RULE_COMMENT

HTTPS = ("443/tcp", "443/udp")  # порт 80 закрыт для скрытности


def session_ssh_port(environ: dict[str, str] | None = None) -> int | None:
    """Порт сервера в текущей SSH-сессии (`SSH_CONNECTION` = «ip порт ip порт»)."""
    parts = (environ if environ is not None else dict(os.environ)).get("SSH_CONNECTION", "").split()
    if len(parts) == 4 and parts[3].isdigit():
        return int(parts[3])
    return None


class FirewallService:
    def __init__(self, app: App) -> None:
        self.app = app

    def setup(self, environ: dict[str, str] | None = None) -> str:
        """Сброс UFW: входящие запрещены, открыты SSH и 443. Порт SSH — из `sshd -T`
        и, для страховки, порт текущей SSH-сессии: установщик не должен отрезать доступ."""
        ports_set = {self.app.sshd.port()}
        session_port = session_ssh_port(environ)
        if session_port:
            ports_set.add(session_port)
        ssh_ports = sorted(ports_set)
        ufw = self.app.ufw
        steps = [
            ufw.reset(),
            ufw.default("deny", "incoming"),
            ufw.default("allow", "outgoing"),
            *(ufw.allow(f"{port}/tcp", SSH_RULE_COMMENT) for port in ssh_ports),
            *(ufw.allow(rule) for rule in HTTPS),
        ]
        failed = [r for r in steps if not r.ok]
        if failed:
            raise ServiceError(f"Не удалось настроить правила UFW: {failed[0].output}")
        res = ufw.enable()
        if not res.ok:
            raise ServiceError(f"Не удалось включить UFW: {res.output}")
        ports = ", ".join(f"{p}/tcp" for p in ssh_ports)
        return (
            f"UFW фаервол настроен (открыты порты: {ports}, 443/tcp, 443/udp; "
            "порт 80 закрыт для скрытности)."
        )
