"""Защита от сканеров. Состояние — по системе (fail2ban), настройка лишь зеркалит его."""

from __future__ import annotations

import os

from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.settings import SettingsService
from kvnbox.system.fail2ban import SecurityError, SecurityStatus


class SecurityService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.settings = SettingsService(app)

    def status(self) -> SecurityStatus:
        return self.app.fail2ban.status()

    def is_installed(self) -> bool:
        return self.app.fail2ban.is_installed()

    def enable(self) -> str:
        try:
            self.app.fail2ban.enable(ssh_port=self.app.sshd.port())
        except SecurityError as e:
            raise ServiceError(str(e)) from e
        self._remember(True)
        return "Защита от сканеров (Fail2ban + Silent Drop) успешно включена."

    def disable(self) -> str:
        self.app.fail2ban.disable()
        self._remember(False)
        return "Защита от сканеров отключена. Все блокировки сняты."

    def unban(self, ip: str) -> str:
        try:
            self.app.fail2ban.unban(ip)
        except SecurityError as e:
            raise ServiceError(str(e)) from e
        return f"IP {ip.strip()} успешно разблокирован."

    def unban_all(self) -> str:
        self.app.fail2ban.unban_all()
        return "Все IP-адреса успешно разблокированы."

    def unban_current_session(self) -> str:
        """Разбан IP, с которого открыта текущая SSH-сессия (SSH_CLIENT)."""
        ip = (os.environ.get("SSH_CLIENT") or os.environ.get("SSH_CONNECTION") or "").split()
        if not ip:
            raise ServiceError("Не удалось определить IP текущей сессии.")
        return self.unban(ip[0])

    def _remember(self, enabled: bool) -> None:
        self.settings.update(
            lambda s: setattr(s.security, "scanner_protection", enabled), apply=False
        )
