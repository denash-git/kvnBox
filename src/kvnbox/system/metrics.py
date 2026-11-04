"""Ресурсы сервера из /proc (без psutil): CPU, память, диск, сеть, аптайм."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kvnbox.core.paths import Paths
from kvnbox.system.files import read_text


@dataclass(frozen=True)
class CpuTimes:
    idle: int
    total: int


@dataclass(frozen=True)
class Memory:
    total_mb: int
    used_mb: int  # как в `free`: total − available
    free_mb: int
    cache_mb: int  # buff/cache
    exact_percent: float = 0.0  # точно, по килобайтам (как psutil) — для порогов алертов

    @property
    def percent(self) -> int:
        return self.used_mb * 100 // self.total_mb if self.total_mb else 0


@dataclass(frozen=True)
class Disk:
    total: int
    used: int
    free: int

    @property
    def percent(self) -> int:
        # как `df`: used / (used + доступно непривилегированным)
        denom = self.used + self.free
        return -(-self.used * 100 // denom) if denom else 0


@dataclass(frozen=True)
class NetCounters:
    iface: str
    rx_bytes: int
    tx_bytes: int


def parse_cpu(stat_text: str) -> CpuTimes:
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
            return CpuTimes(idle=idle, total=sum(values[:8]))
    raise ValueError("в /proc/stat нет строки cpu")


def cpu_percent(a: CpuTimes, b: CpuTimes) -> int:
    total = b.total - a.total
    return max(0, min(100, (total - (b.idle - a.idle)) * 100 // total)) if total > 0 else 0


def parse_meminfo(text: str) -> Memory:
    kb: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            kb[key.strip()] = int(parts[0])
    total = kb.get("MemTotal", 0)
    available = kb.get("MemAvailable", kb.get("MemFree", 0))
    cache = kb.get("Buffers", 0) + kb.get("Cached", 0) + kb.get("SReclaimable", 0)
    return Memory(
        total_mb=total // 1024,
        used_mb=(total - available) // 1024,
        free_mb=kb.get("MemFree", 0) // 1024,
        cache_mb=cache // 1024,
        exact_percent=round((total - available) * 100 / total, 1) if total else 0.0,
    )


def parse_net_dev(text: str, iface: str) -> NetCounters | None:
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if sep and name.strip() == iface:
            v = rest.split()
            return NetCounters(iface, rx_bytes=int(v[0]), tx_bytes=int(v[8]))
    return None


def parse_default_iface(route_text: str) -> str | None:
    """/proc/net/route: интерфейс маршрута с Destination 00000000."""
    for line in route_text.splitlines()[1:]:
        cols = line.split()
        if len(cols) > 2 and cols[1] == "00000000":
            return cols[0]
    return None


def format_uptime(seconds: float) -> str:
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes = s // 60
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


class Metrics:
    def __init__(self, paths: Paths, disk_path: Path = Path("/")) -> None:
        self.proc = paths.proc
        self.disk_path = disk_path

    def _read(self, *parts: str) -> str:
        return read_text(self.proc.joinpath(*parts)) or ""

    def cpu_times(self) -> CpuTimes:
        return parse_cpu(self._read("stat"))

    def memory(self) -> Memory:
        return parse_meminfo(self._read("meminfo"))

    def disk(self) -> Disk:
        st = os.statvfs(self.disk_path)  # type: ignore[attr-defined,unused-ignore]
        total = st.f_blocks * st.f_frsize
        free_root = st.f_bfree * st.f_frsize
        return Disk(total=total, used=total - free_root, free=st.f_bavail * st.f_frsize)

    def default_iface(self) -> str:
        return parse_default_iface(self._read("net", "route")) or "eth0"

    def net(self, iface: str | None = None) -> NetCounters | None:
        return parse_net_dev(self._read("net", "dev"), iface or self.default_iface())

    def uptime_seconds(self) -> float:
        text = self._read("uptime").split()
        return float(text[0]) if text else 0.0

    def load_avg(self) -> tuple[float, float, float]:
        v = self._read("loadavg").split()
        return (float(v[0]), float(v[1]), float(v[2])) if len(v) >= 3 else (0.0, 0.0, 0.0)
