"""Тест CDN (Диагностика): сервер сам идёт на домен CDN, CDN приходит обратно на
origin-поддомен этого же сервера (ответ /health), время меряется по одним часам.

Для каждого запроса через CDN:
  * до CDN      — TCP и TLS до узла CDN (curl: time_connect, time_appconnect);
  * туда        — от отправки запроса до прихода на origin (заголовок X-Origin-Time
                  ставит Caddy по часам этого же сервера);
  * обратно     — от origin до первого байта ответа у сервера;
  * всего       — полное время запроса.
Сначала — тот же ответ напрямую с origin (127.0.0.1): сертификат и маршрут Caddy.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from kvnbox.cores.caddy import CDN_HEALTH_BODY, CDN_HEALTH_PATH, CDN_TIME_HEADER
from kvnbox.services import ServiceError
from kvnbox.services.app import App

ROUNDS = 5
TIMEOUT = 15
PAUSE = 1.0
_SEP = "|"
# поля -w curl, через «|»; тело ответа — до них, отдельной строкой
_FORMAT = _SEP.join(
    [
        "%{http_code}",
        "%{time_connect}",
        "%{time_appconnect}",
        "%{time_pretransfer}",
        "%{time_starttransfer}",
        "%{time_total}",
        "%{remote_ip}",
        "%header{" + CDN_TIME_HEADER.lower() + "}",
        "%header{cache-host}",
    ]
)


@dataclass(frozen=True)
class Probe:
    ok: bool
    error: str = ""
    edge_ip: str = ""
    node: str = ""  # имя узла CDN (заголовок Cache-Host у Яндекса)
    connect_ms: int | None = None
    tls_ms: int | None = None
    to_origin_ms: int | None = None
    back_ms: int | None = None
    total_ms: int | None = None


@dataclass
class CdnReport:
    domain: str
    origin: str
    direct: Probe
    probes: list[Probe] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(p.ok for p in self.probes)


def _ms(seconds: str) -> int:
    return round(float(seconds) * 1000)


class CdnTest:
    def __init__(self, app: App, clock: Callable[[], float] = time.time) -> None:
        self.app = app
        self.clock = clock

    def run(self, rounds: int = ROUNDS, pause: float = PAUSE) -> CdnReport:
        c = self.app.settings.load().cdn
        if not c.configured:
            raise ServiceError("CDN не настроен: Модули → Настройки CDN.")
        local = ["--resolve", f"{c.origin_host}:443:127.0.0.1"]
        report = CdnReport(c.domain, c.origin_host, self.probe(c.origin_host, local))
        for i in range(rounds):
            if i and pause:
                time.sleep(pause)
            report.probes.append(self.probe(c.domain, []))
        return report

    def probe(self, host: str, extra: list[str]) -> Probe:
        url = f"https://{host}{CDN_HEALTH_PATH}"
        argv = ["curl", "-sS", "-m", str(TIMEOUT), "-o", "-", "-w", "\n" + _FORMAT, *extra, url]
        start = self.clock()
        res = self.app.runner.run(argv, timeout=TIMEOUT + 10)
        if not res.ok:
            lines = res.output.splitlines()
            return Probe(False, lines[-1] if lines else f"curl: код {res.returncode}")
        body, _, tail = res.stdout.rstrip("\n").rpartition("\n")
        f = tail.split(_SEP)
        if len(f) < 9:
            return Probe(False, "непонятный ответ curl")
        code, conn, tls, pre, first, total, ip, origin_ts, node = f[:9]
        if code != "200" or body.strip() != CDN_HEALTH_BODY:
            return Probe(False, f"HTTP {code}", ip, node.strip())
        to_origin = back = None
        if origin_ts.strip().isdigit():
            arrived = int(origin_ts) / 1000
            to_origin = round((arrived - start - float(pre)) * 1000)
            back = round((start + float(first) - arrived) * 1000)
        return Probe(
            True,
            edge_ip=ip,
            node=node.strip(),
            connect_ms=_ms(conn),
            tls_ms=_ms(tls) - _ms(conn),
            to_origin_ms=to_origin,
            back_ms=back,
            total_ms=_ms(total),
        )


# ─── текст отчёта (меню, бот, консоль) ───────────────────────────────────────


def _v(x: int | None) -> str:
    return "—" if x is None else str(x)


def _stat(values: list[int]) -> str:
    if not values:
        return "—"
    return f"{round(statistics.median(values))} / {max(values)}"


def report_lines(r: CdnReport) -> list[str]:
    """Строки отчёта без оформления: первая — итог."""
    lines = [f"Через CDN: {r.ok_count} из {len(r.probes)} запросов успешно", ""]
    d = r.direct
    lines.append(
        f"Origin {r.origin} напрямую: "
        + (f"OK, {_v(d.total_ms)} мс" if d.ok else f"ошибка — {d.error}")
    )
    lines.append(f"Домен CDN: {r.domain}")
    lines.append("")
    lines.append(
        f"{'№':<2} {'Узел CDN':<18} {'До CDN':>6} {'TLS':>4} {'Запрос':>6} {'Ответ':>6} "
        f"{'Всего':>6}, мс"
    )
    for i, p in enumerate(r.probes, 1):
        node = (p.node or p.edge_ip or "—")[:18]
        if p.ok:
            lines.append(
                f"{i:<2} {node:<18} {_v(p.connect_ms):>6} {_v(p.tls_ms):>4} "
                f"{_v(p.to_origin_ms):>6} {_v(p.back_ms):>6} {_v(p.total_ms):>6}"
            )
        else:
            lines.append(f"{i:<2} {node:<18} ✗ {p.error}")
    good = [p for p in r.probes if p.ok]
    if good:
        lines.append("")
        lines.append("Медиана / максимум, мс:")
        for label, attr in (
            ("До CDN, TCP", "connect_ms"),
            ("TLS", "tls_ms"),
            ("Запрос vps → cdn → origin", "to_origin_ms"),
            ("Ответ origin → cdn → vps", "back_ms"),
            ("Всего", "total_ms"),
        ):
            vals = [v for p in good if (v := getattr(p, attr)) is not None]
            lines.append(f"  {label:<26} {_stat(vals)}")
    if d.ok and r.probes and not good:
        lines.append("")
        lines.append("Сервер отвечает, CDN — нет: проверьте в ресурсе CDN источник,")
        lines.append("протокол HTTPS к источнику и заголовок Host = origin-поддомен.")
    return lines
