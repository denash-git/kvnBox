"""Служба systemd для Caddy.

Файл службы принадлежит панели: его пишет `ensure_caddy_unit` (из установщика и
при каждом `apply`), поэтому исправления доходят и через обновление из меню.

Без HOME Caddy хранит сертификаты в «./caddy» относительно рабочего каталога
службы, то есть в `/caddy` (так было в TransferBox и kvnBox 0.1.0). Теперь —
`/var/lib/caddy` (XDG_DATA_HOME / XDG_CONFIG_HOME); старый каталог переносится,
чтобы не выпускать сертификаты заново.
"""

from __future__ import annotations

import shutil

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.systemd import Systemd

CADDY_BIN = "/usr/local/bin/caddy"

UNIT = f"""[Unit]
Description=Caddy Frontend Service
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=notify
Environment=XDG_DATA_HOME=/var/lib XDG_CONFIG_HOME=/var/lib
ExecStart={CADDY_BIN} run --config /etc/caddy/Caddyfile --environ
ExecReload={CADDY_BIN} reload --config /etc/caddy/Caddyfile --force
ExecStop={CADDY_BIN} stop
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""


def ensure_caddy_unit(paths: Paths, systemd: Systemd) -> bool:
    """Привести службу к нужному виду. True — что-то изменилось (нужен перезапуск caddy)."""
    changed = False
    legacy, data = paths.caddy_legacy_data, paths.caddy_data
    if legacy.is_dir() and not data.exists():
        # работающий caddy со старым окружением снова создал бы /caddy;
        # после этой функции его всё равно запускают заново
        systemd.stop("caddy")
        data.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(data))
        changed = True
    if read_text(paths.caddy_unit) != UNIT:
        atomic_write_text(paths.caddy_unit, UNIT, 0o644)
        systemd.daemon_reload()
        changed = True
    return changed
