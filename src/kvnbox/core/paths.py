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

    @classmethod
    def from_env(cls) -> Paths:
        return cls(
            root=Path(os.environ.get("KVNBOX_ROOT", str(DEFAULT_ROOT))),
            etc=Path(os.environ.get("KVNBOX_ETC", str(DEFAULT_ETC))),
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
