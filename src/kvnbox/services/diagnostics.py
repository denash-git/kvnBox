"""Диагностика: логи служб, ресурсы, замер скорости (Ookla, iperf3)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.system.metrics import Disk, Memory, cpu_percent, format_uptime

LOG_UNITS = {"Caddy": "caddy", "sing-box": "sing-box", "Mieru": "mita", "NetBird": "netbird"}
IPERF_PORT = 5201
IPERF_UNIT = "kvnbox-iperf3"
SPEEDTEST_FLAGS = ["--accept-license", "--accept-gdpr"]


@dataclass(frozen=True)
class Snapshot:
    cpu_percent: int
    memory: Memory
    disk: Disk
    iface: str
    rx_per_s: int  # байт/с
    tx_per_s: int
    uptime: str
    load: tuple[float, float, float]


@dataclass(frozen=True)
class SpeedtestServer:
    id: str
    name: str
    location: str
    country: str = ""


class DiagnosticsService:
    def __init__(self, app: App) -> None:
        self.app = app

    def logs(self, unit: str, lines: int = 100) -> str:
        return self.app.systemd.journal(unit, lines)

    def snapshot(self, interval: float = 1.0) -> Snapshot:
        m = self.app.metrics
        iface = m.default_iface()
        cpu1, net1 = m.cpu_times(), m.net(iface)
        time.sleep(interval)
        cpu2, net2 = m.cpu_times(), m.net(iface)
        rx = tx = 0
        if net1 and net2 and interval > 0:
            rx = int((net2.rx_bytes - net1.rx_bytes) / interval)
            tx = int((net2.tx_bytes - net1.tx_bytes) / interval)
        return Snapshot(
            cpu_percent=cpu_percent(cpu1, cpu2),
            memory=m.memory(),
            disk=m.disk(),
            iface=iface,
            rx_per_s=max(rx, 0),
            tx_per_s=max(tx, 0),
            uptime=format_uptime(m.uptime_seconds()),
            load=m.load_avg(),
        )

    # ── Ookla speedtest ──────────────────────────────────────────────────────

    def _require_speedtest(self) -> None:
        if not self.app.installed("speedtest"):
            raise ServiceError("Ookla speedtest не установлен (ставится установщиком панели).")

    def speedtest_servers(self) -> list[SpeedtestServer]:
        self._require_speedtest()
        res = self.app.runner.run(["speedtest", "-L", "-f", "json", *SPEEDTEST_FLAGS], timeout=60)
        try:
            servers = json.loads(res.stdout).get("servers", [])
        except (json.JSONDecodeError, AttributeError) as e:
            raise ServiceError(f"speedtest не вернул список серверов: {res.output}") from e
        return [
            SpeedtestServer(
                str(s.get("id", "")),
                str(s.get("name", "")),
                str(s.get("location", "")),
                str(s.get("country", "")),
            )
            for s in servers
        ]

    def speedtest(self, server_id: str | None = None) -> str:
        self._require_speedtest()
        argv = ["speedtest", *SPEEDTEST_FLAGS]
        if server_id:
            if not server_id.isdigit():
                raise ServiceError("Неверный ID сервера.")
            argv += ["-s", server_id]
        res = self.app.runner.run(argv, timeout=180)
        # при первом запуске Ookla пишет лицензию в stderr — результат в stdout
        # (`.output` отдал бы лицензию вместо замера; приёмка, этап 9)
        return res.stdout if res.ok and res.stdout.strip() else res.output

    def speedtest_json(self) -> str:
        """Для бота: JSON Ookla, при неудаче — обычный текстовый вывод."""
        self._require_speedtest()
        res = self.app.runner.run(["speedtest", *SPEEDTEST_FLAGS, "--format=json"], timeout=180)
        if res.ok and res.stdout.strip():
            return res.stdout
        res = self.app.runner.run(["speedtest", *SPEEDTEST_FLAGS], timeout=180)
        if not res.ok:
            raise ServiceError(res.output or "speedtest завершился с ошибкой")
        return res.stdout or res.stderr

    # ── iperf3 (клиент ↔ VPS) ────────────────────────────────────────────────

    def public_ip(self) -> str:
        for url in ("https://ifconfig.me", "https://api.ipify.org"):
            res = self.app.runner.run(["curl", "-4", "-s", "--max-time", "5", url], timeout=10)
            if res.ok and res.stdout.strip():
                return res.stdout.strip()
        return ""

    def iperf_start(self) -> int:
        """Запустить iperf3-сервер как временную службу и открыть порт. Возвращает порт."""
        if not self.app.installed("iperf3"):
            raise ServiceError("iperf3 не установлен (ставится установщиком панели).")
        self.iperf_stop()
        self.app.ufw.allow(f"{IPERF_PORT}/tcp", "iperf3 TCP")
        self.app.ufw.allow(f"{IPERF_PORT}/udp", "iperf3 UDP")
        res = self.app.runner.run(
            [
                "systemd-run",
                f"--unit={IPERF_UNIT}",
                "--collect",
                "iperf3",
                "-s",
                "-p",
                str(IPERF_PORT),
            ],
            timeout=30,
        )
        if not res.ok:
            self.iperf_stop()
            raise ServiceError(f"Не удалось запустить iperf3: {res.output}")
        return IPERF_PORT

    def iperf_running(self) -> bool:
        return self.app.systemd.is_active(IPERF_UNIT)

    def iperf_stop(self) -> None:
        self.app.systemd.stop(IPERF_UNIT)
        self.app.ufw.delete_by_comment("iperf3 TCP", "iperf3 UDP")
