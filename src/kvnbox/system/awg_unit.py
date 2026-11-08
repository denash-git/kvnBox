"""Служба systemd AmneziaWG (этап 13): `amneziawg-go` на переднем плане + настройка
интерфейса командой панели после старта (ключи, клиенты, адрес, NAT) — так после
перезагрузки сервера всё поднимается само.

Ограничение памяти: на сервере с 1 ГБ памяти без подкачки два `amneziawg-go` под нагрузкой
~600 Мбит/с довели сервер до зависания — служба не должна забирать всю память.
"""

from __future__ import annotations

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.awg import INTERFACE
from kvnbox.system.files import read_text
from kvnbox.system.systemd import Systemd

UNIT_NAME = "kvnbox-awg"
CLI = "/usr/local/bin/kvnbox"


def unit_text(paths: Paths) -> str:
    return f"""[Unit]
Description=AmneziaWG 3.1 (kvnBox)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=GOMEMLIMIT=120MiB
ExecStart={paths.awg_bin.as_posix()} -f {INTERFACE}
ExecStartPost={CLI} awg sync
ExecStopPost={CLI} awg down
Restart=on-failure
RestartSec=5s
MemoryHigh=160M
MemoryMax=250M

[Install]
WantedBy=multi-user.target
"""


def ensure_awg_unit(paths: Paths, systemd: Systemd) -> bool:
    """Записать службу, если её нет или она другая. True — если файл изменён."""
    text = unit_text(paths)
    if read_text(paths.awg_unit) == text:
        return False
    atomic_write_text(paths.awg_unit, text, 0o644)
    systemd.daemon_reload()
    return True
