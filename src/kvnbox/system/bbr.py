"""TCP BBR: состояние читается из ядра, включение — через sysctl.d."""

from __future__ import annotations

import re
from pathlib import Path

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.runner import Runner

CONF = "net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n"
_BBR_LINE = re.compile(r"^\s*net[./]ipv4[./]tcp_congestion_control\s*=\s*bbr\s*$", re.M)


class Bbr:
    def __init__(self, runner: Runner, paths: Paths) -> None:
        self.runner = runner
        self.paths = paths

    def _proc(self, *parts: str) -> str:
        return (read_text(self.paths.proc.joinpath("sys", *parts)) or "").strip()

    def is_active(self) -> bool:
        return self._proc("net", "ipv4", "tcp_congestion_control") == "bbr"

    def is_supported(self) -> bool:
        available = self._proc("net", "ipv4", "tcp_available_congestion_control").split()
        return "bbr" in available or self.runner.run(["modprobe", "tcp_bbr"], timeout=15).ok

    def _own_files(self) -> tuple[Path, ...]:
        """Наш файл и файл TransferBox (на мигрированном сервере)."""
        return (self.paths.sysctl_bbr, self.paths.etc / "sysctl.d" / "99-transferbox-bbr.conf")

    def foreign_sources(self) -> list[Path]:
        """Чужие конфиги sysctl, которые включат BBR обратно при перезагрузке."""
        candidates = [self.paths.etc / "sysctl.conf"]
        sysctl_d = self.paths.etc / "sysctl.d"
        if sysctl_d.is_dir():
            candidates += sorted(sysctl_d.glob("*.conf"))
        own = set(self._own_files())
        return [p for p in candidates if p not in own and _BBR_LINE.search(read_text(p) or "")]

    def enable(self) -> bool:
        """Включить BBR сейчас и после перезагрузки. False — ядро не поддерживает."""
        if not self.is_supported():
            return False
        atomic_write_text(self.paths.sysctl_bbr, CONF, 0o644)
        self.paths.etc.joinpath("sysctl.d", "99-transferbox-bbr.conf").unlink(missing_ok=True)
        self.runner.run(["sysctl", "-w", "net.core.default_qdisc=fq"], timeout=15)
        self.runner.run(["sysctl", "-w", "net.ipv4.tcp_congestion_control=bbr"], timeout=15)
        return True

    def disable(self) -> bool:
        """Вернуть cubic (как TransferBox); очередь (qdisc) не трогаем.

        Сначала убрать файлы с bbr и перечитать sysctl.d, и только потом
        выставить cubic: иначе `sysctl --system` вернёт bbr из оставшегося
        файла (так было на сервере после TransferBox). True — только если ядро
        действительно переключилось.
        """
        for path in self._own_files():
            path.unlink(missing_ok=True)
        self.runner.run(["sysctl", "--system"], timeout=30)
        res = self.runner.run(["sysctl", "-w", "net.ipv4.tcp_congestion_control=cubic"], timeout=15)
        return res.ok and not self.is_active()
