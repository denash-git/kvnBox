"""Все файловые пути kvnBox в одном месте.

На сервере корень проекта — `/opt/kvnbox`, системные конфиги — в `/etc`.
В тестах оба корня подменяются через `Paths(root=..., etc=...)` или переменные
окружения `KVNBOX_ROOT` / `KVNBOX_ETC`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path("/opt/kvnbox")
DEFAULT_ETC = Path("/etc")


@dataclass(frozen=True)
class Paths:
    root: Path = DEFAULT_ROOT
    etc: Path = DEFAULT_ETC
    proc: Path = Path("/proc")
    home: Path = Path("/root")  # домашний каталог root (authorized_keys)

    @classmethod
    def from_env(cls) -> Paths:
        return cls(
            root=Path(os.environ.get("KVNBOX_ROOT", str(DEFAULT_ROOT))),
            etc=Path(os.environ.get("KVNBOX_ETC", str(DEFAULT_ETC))),
            proc=Path(os.environ.get("KVNBOX_PROC", "/proc")),
            home=Path(os.environ.get("KVNBOX_HOME", "/root")),
        )

    # ── данные kvnBox ────────────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def users_file(self) -> Path:
        return self.data_dir / "users.json"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def sub_dir(self) -> Path:
        """Файлы подписок, раздаются Caddy по `/sub/<token>`."""
        return self.root / "sub"

    @property
    def fakesite_dir(self) -> Path:
        return self.root / "fakesite"

    # ── код панели (D-039) ───────────────────────────────────────────────────
    @property
    def releases_dir(self) -> Path:
        """Распакованные версии панели: `releases/<версия>/src/kvnbox`."""
        return self.root / "releases"

    @property
    def app_link(self) -> Path:
        """Ссылка на текущую версию в `releases/` (её `src` подключён к venv через .pth)."""
        return self.root / "app"

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    # ── конфиги ядер ─────────────────────────────────────────────────────────
    @property
    def caddy_config(self) -> Path:
        return self.etc / "caddy" / "Caddyfile"

    @property
    def singbox_config(self) -> Path:
        return self.etc / "sing-box" / "config.json"

    @property
    def mita_config(self) -> Path:
        return self.etc / "mita" / "server.conf.json"

    @property
    def mita_service_override(self) -> Path:
        return self.etc / "systemd" / "system" / "mita.service.d" / "override.conf"

    # ── SSH ──────────────────────────────────────────────────────────────────
    @property
    def sshd_config(self) -> Path:
        return self.etc / "ssh" / "sshd_config"

    @property
    def sshd_dropin_dir(self) -> Path:
        return self.etc / "ssh" / "sshd_config.d"

    @property
    def ssh_auth_dropin(self) -> Path:
        # «00-» — раньше файлов провайдера (00password.conf и т.п.): у sshd
        # выигрывает первое встреченное значение
        return self.sshd_dropin_dir / "00-kvnbox-auth.conf"

    @property
    def authorized_keys(self) -> Path:
        return self.home / ".ssh" / "authorized_keys"

    # ── защита от сканеров ───────────────────────────────────────────────────
    @property
    def fail2ban_filter(self) -> Path:
        return self.etc / "fail2ban" / "filter.d" / "portscan-trap.conf"

    @property
    def fail2ban_jail(self) -> Path:
        return self.etc / "fail2ban" / "jail.d" / "kvnbox.conf"

    @property
    def ufw_before_rules(self) -> Path:
        return self.etc / "ufw" / "before.rules"

    @property
    def ufw_before6_rules(self) -> Path:
        return self.etc / "ufw" / "before6.rules"

    # ── сеть ─────────────────────────────────────────────────────────────────
    @property
    def sysctl_bbr(self) -> Path:
        return self.etc / "sysctl.d" / "99-kvnbox-bbr.conf"

    # ── файлы TransferBox на том же сервере (наследие) ───────────────────────
    @property
    def legacy_files(self) -> tuple[Path, ...]:
        return (
            self.sshd_dropin_dir / "00-transferbox-auth.conf",
            self.sshd_dropin_dir / "99-transferbox-auth.conf",
            self.etc / "fail2ban" / "jail.d" / "transferbox.conf",
            self.etc / "sysctl.d" / "99-transferbox-bbr.conf",
        )
