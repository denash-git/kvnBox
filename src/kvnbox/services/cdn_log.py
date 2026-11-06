"""«Журнал CDN» (Диагностика): на время теста Caddy пишет журнал только CDN-запросов,
владелец в это время подключается с телефона, затем панель разбирает журнал по сессиям.

Сессия XHTTP (packet-up) в журнале:
  * GET <путь>/<сессия>          — долгий GET загрузки (ответ сервера идёт по нему);
  * GET <путь>/<сессия>/<номер>  — запросы отдачи (данные клиента в заголовках).
Если CDN придерживает запросы отдачи, пока открыт долгий GET, они приходят с паузой
или уже после его закрытия — тогда клиент не дожидается и бросает попытку.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from kvnbox import __version__
from kvnbox.core.storage import atomic_write_text
from kvnbox.cores.caddy import render_caddyfile
from kvnbox.cores.xray import cdn_active
from kvnbox.services import ServiceError
from kvnbox.services.app import App

DURATION = 120
EARLY = 15.0  # секунд от начала сессии: в это время запросы отдачи идут подряд
GAP_WARN = 2.0  # пауза между запросами отдачи в начале сессии — уже подозрительно
MAX_SESSIONS = 40
REPORT_NAME = "cdn-log-report.txt"


@dataclass
class _Req:
    start: float
    seq: int | None  # None — долгий GET загрузки
    status: int
    size: int
    duration: float
    ip: str


@dataclass
class Session:
    sid: str
    reqs: list[_Req] = field(default_factory=list)

    @property
    def t0(self) -> float:
        return min(r.start for r in self.reqs)

    @property
    def get(self) -> _Req | None:
        downs = [r for r in self.reqs if r.seq is None]
        return min(downs, key=lambda r: r.start) if downs else None

    @property
    def ups(self) -> list[_Req]:
        return sorted((r for r in self.reqs if r.seq is not None), key=lambda r: r.start)


def parse(lines: Iterable[str], cdn_path: str) -> list[Session]:
    """Строки JSON-журнала Caddy → сессии XHTTP (по идентификатору в пути)."""
    prefix = cdn_path.rstrip("/") + "/"
    sessions: dict[str, Session] = {}
    for line in lines:
        try:
            r = json.loads(line)
            q = r["request"]
            uri = str(q["uri"]).split("?", 1)[0]
            dur = float(r.get("duration", 0))
            start = float(r["ts"]) - dur
        except (ValueError, KeyError, TypeError):
            continue
        if not uri.startswith(prefix):
            continue
        parts = uri[len(prefix) :].strip("/").split("/")
        if not parts or not parts[0]:
            continue
        seq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        if len(parts) > 1 and seq is None:
            continue
        s = sessions.setdefault(parts[0], Session(parts[0]))
        s.reqs.append(
            _Req(
                start,
                seq,
                int(r.get("status", 0)),
                int(r.get("size", 0)),
                dur,
                q.get("remote_ip", ""),
            )
        )
    return sorted(sessions.values(), key=lambda s: s.t0)


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}МБ"
    if n >= 1024:
        return f"{n / 1024:.1f}КБ"
    return f"{n}Б"


def _clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def verdict(s: Session) -> tuple[str, str]:
    """(значок, текст) по сессии: ✓ — нормально, ⚠ — задержки, ✗ — оборвалась."""
    get, ups, t0 = s.get, s.ups, s.t0
    bad_status = [r for r in s.reqs if r.status != 200]
    parts: list[str] = []
    if get is None:
        parts.append("GET загрузки не пришёл")
    else:
        parts.append(
            f"GET +{get.start - t0:.1f}с, {get.duration:.1f}с, {_size(get.size)}"
            + ("" if get.status == 200 else f", код {get.status}")
        )
    late: list[_Req] = []
    gap, gap_seq = 0.0, None
    if ups:
        prev = t0
        for r in ups:
            if r.start - t0 <= EARLY and r.start - prev > gap:
                gap, gap_seq = r.start - prev, r.seq
            prev = r.start
        if get is not None:
            end = get.start + get.duration
            late = [r for r in ups if r.start > end + 0.2]
        text = f"отдача {len(ups)}"
        if gap >= GAP_WARN:
            text += f", пауза {gap:.1f}с перед №{gap_seq}"
        else:
            text += f" (макс. пауза в начале {gap:.1f}с)"
        parts.append(text)
    else:
        parts.append("отдачи нет")
    if late:
        first = late[0]
        parts.append(
            f"{len(late)} запр. отдачи пришли после конца GET"
            f" (№{first.seq} через {first.start - t0:.1f}с)"
        )
    if bad_status:
        codes = Counter(r.status for r in bad_status)
        parts.append("коды " + ", ".join(f"{c}×{n}" for c, n in codes.items()))
    if get is None or late or bad_status:
        icon = "✗"
    elif gap >= GAP_WARN:
        icon = "⚠"
    else:
        icon = "✓"
    return icon, "; ".join(parts)


def report(sessions: list[Session], started: float, finished: float, domain: str) -> str:
    icons = [verdict(s)[0] for s in sessions]
    ips = Counter(r.ip for s in sessions for r in s.reqs)
    total = sum(len(s.reqs) for s in sessions)
    lines = [
        f"Журнал CDN · kvnBox {__version__} · {domain}",
        f"{dt.datetime.fromtimestamp(started):%Y-%m-%d %H:%M:%S}–{_clock(finished)}"
        f" ({finished - started:.0f} с)",
        f"Сессий: {len(sessions)} — ✓ {icons.count('✓')}, ⚠ {icons.count('⚠')},"
        f" ✗ {icons.count('✗')}; запросов: {total}",
    ]
    if ips:
        lines.append("Узлы CDN: " + ", ".join(f"{ip}×{n}" for ip, n in ips.most_common(5)))
    lines.append("")
    if not sessions:
        lines.append("Запросов CDN не было: до сервера не дошла ни одна попытка подключения.")
        return "\n".join(lines)
    lines.append(
        "+с — от начала сессии; GET — долгий запрос загрузки; отдача — запросы с данными клиента"
    )
    shown = sessions[-MAX_SESSIONS:]
    if len(sessions) > len(shown):
        lines.append(f"(показаны последние {len(shown)} из {len(sessions)})")
    for i, s in enumerate(shown, len(sessions) - len(shown) + 1):
        icon, text = verdict(s)
        lines.append(f"#{i} {_clock(s.t0)} {icon} {text}")
    return "\n".join(lines)


class CdnLog:
    def __init__(self, app: App) -> None:
        self.app = app

    def check(self) -> None:
        s = self.app.settings.load()
        if not s.cdn.configured:
            raise ServiceError("CDN не настроен: Модули → Настройки CDN.")
        if not cdn_active(s, self.app.users.all()):
            raise ServiceError("Нет включённых подключений VLESS CDN — журналу нечего показать.")

    def start(self) -> Path:
        """Включить журнал. Имя файла каждый раз новое: Caddy держит файл журнала
        открытым между перезагрузками, удалённый файл с тем же именем не пересоздаётся."""
        self.check()
        path = self.app.paths.root / f"cdn-log-{int(time.time())}.jsonl"
        self._caddy(path)
        return path

    def stop(self) -> None:
        self._caddy(None)

    def _caddy(self, log_file: Path | None) -> None:
        s = self.app.settings.load()
        text = render_caddyfile(s, self.app.users.all(), self.app.paths, cdn_log=log_file)
        staging = self.app.paths.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        tmp = staging / "Caddyfile"
        atomic_write_text(tmp, text)
        res = self.app.runner.run(
            ["caddy", "validate", "--adapter", "caddyfile", "--config", str(tmp)], timeout=60
        )
        if not res.ok:
            raise ServiceError(f"Ошибка в конфигурации Caddy:\n{res.output}")
        atomic_write_text(self.app.paths.caddy_config, text, 0o600)
        res = self.app.systemd.reload_or_restart("caddy")
        if not res.ok:
            raise ServiceError(f"Не удалось перезагрузить Caddy: {res.output}")

    def collect(self, path: Path, started: float, finished: float) -> str:
        """Разобрать журнал, удалить его, сохранить отчёт рядом (чтобы скопировать)."""
        s = self.app.settings.load()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        path.unlink(missing_ok=True)
        text = report(parse(lines, s.xray.cdn_path), started, finished, s.cdn.domain)
        atomic_write_text(self.app.paths.root / REPORT_NAME, text + "\n", 0o600)
        return text

    def run(self, seconds: int = DURATION, wait: Callable[[float], None] = time.sleep) -> str:
        path = self.start()
        started = time.time()
        try:
            wait(seconds)
        finally:
            self.stop()
        return self.collect(path, started, time.time())

    @property
    def report_path(self) -> Path:
        return self.app.paths.root / REPORT_NAME
