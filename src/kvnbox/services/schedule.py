"""Регулярные задачи: /etc/cron.d/kvnbox.

Проверка служб (каждые 5 минут) и обновлений (ежедневно) — как в TransferBox;
автобэкап — в заданное время, если включён. Команды — подкоманды CLI `kvnbox job …`.
"""

from __future__ import annotations

from kvnbox.core.models import Settings
from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text


def cron_content(settings: Settings, paths: Paths) -> str:
    cli = (paths.root / ".venv" / "bin" / "kvnbox").as_posix()
    lines = [
        "# kvnBox: регулярные задачи (файл создаётся панелью, не редактируйте вручную)",
        "SHELL=/bin/sh",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        f"*/5 * * * * root {cli} job check-services >/dev/null 2>&1",
        f"0 0 * * * root {cli} job check-updates >/dev/null 2>&1",
        f"0 10 * * * root {cli} job check-cert >/dev/null 2>&1",
    ]
    b = settings.backup
    if b.enabled:
        lines.append(f"{b.minute} {b.hour} * * * root {cli} job backup >/dev/null 2>&1")
    return "\n".join(lines) + "\n"


def write_cron(settings: Settings, paths: Paths) -> None:
    atomic_write_text(paths.etc / "cron.d" / "kvnbox", cron_content(settings, paths), 0o644)
