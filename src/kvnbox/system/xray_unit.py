"""Служба systemd для Xray (этап 11). Файл принадлежит панели, как служба caddy.

Xray слушает только 127.0.0.1 и работает от системного пользователя `xray`
без прав root.
"""

from __future__ import annotations

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.systemd import Systemd

UNIT_NAME = "xray"


def unit_text(paths: Paths) -> str:
    return f"""[Unit]
Description=Xray (kvnBox)
After=network.target network-online.target

[Service]
User=xray
Group=xray
ExecStart={paths.xray_bin.as_posix()} run -config {paths.xray_config.as_posix()}
Restart=on-failure
RestartSec=5s
LimitNOFILE=1048576
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def ensure_xray_unit(paths: Paths, systemd: Systemd) -> bool:
    """Записать службу, если её нет или она другая. True — если файл изменён."""
    text = unit_text(paths)
    if read_text(paths.xray_unit) == text:
        return False
    atomic_write_text(paths.xray_unit, text, 0o644)
    systemd.daemon_reload()
    return True
