"""TCP BBR: состояние читается из ядра, включение — через sysctl.d."""

from __future__ import annotations

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.runner import Runner

CONF = "net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n"


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

    def enable(self) -> bool:
        """Включить BBR сейчас и после перезагрузки. False — ядро не поддерживает."""
        if not self.is_supported():
            return False
        atomic_write_text(self.paths.sysctl_bbr, CONF, 0o644)
        self.runner.run(["sysctl", "-w", "net.core.default_qdisc=fq"], timeout=15)
        self.runner.run(["sysctl", "-w", "net.ipv4.tcp_congestion_control=bbr"], timeout=15)
        return True

    def disable(self) -> bool:
        """Вернуть cubic (как TransferBox); очередь (qdisc) не трогаем."""
        res = self.runner.run(["sysctl", "-w", "net.ipv4.tcp_congestion_control=cubic"], timeout=15)
        if not res.ok:
            return False
        self.paths.sysctl_bbr.unlink(missing_ok=True)
        self.runner.run(["sysctl", "--system"], timeout=30)
        return True
