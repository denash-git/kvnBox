from __future__ import annotations

import json

import pytest

from kvnbox import __version__
from kvnbox.core.models import Protocol, TelegramSettings
from kvnbox.services import ServiceError
from kvnbox.services.jobs import Jobs
from kvnbox.services.notify import NotifyError, Telegram
from kvnbox.system.releases import Releases
from kvnbox.system.runner import Result
from tests.conftest import Env


class Sent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    def __call__(self, url: str, body: bytes, content_type: str) -> bytes:
        self.calls.append((url, body))
        return b'{"ok": true}'

    @property
    def texts(self) -> list[str]:
        return [json.loads(b)["text"] for u, b in self.calls if u.endswith("sendMessage")]


def telegram(sent: Sent, alerts: bool = True) -> Telegram:
    return Telegram(
        TelegramSettings(enabled=True, token="1:T", chat_id="42", alerts_enabled=alerts), post=sent
    )


def proc(env: Env, cpu: tuple[int, int], mem_pct: int) -> None:
    p = env.paths.proc
    p.mkdir(parents=True, exist_ok=True)
    busy, idle = cpu
    (p / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\n")
    total = 1_000_000
    (p / "meminfo").write_text(
        f"MemTotal: {total} kB\nMemFree: 1 kB\nMemAvailable: {total * (100 - mem_pct) // 100} kB\n"
    )


def units(env: Env, states: dict[str, str]) -> None:
    for unit, state in states.items():
        env.runner.on(
            "systemctl",
            "list-unit-files",
            f"{unit}.service",
            result=Result(0, f"{unit}.service enabled\n"),
        )
        env.runner.on(
            "systemctl",
            "is-active",
            unit,
            result=Result(0 if state == "active" else 3, state + "\n"),
        )
    env.runner.on("systemctl", "list-unit-files", result=Result(0, "0 unit files listed.\n"))


@pytest.fixture
def jobs_env(env: Env, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr("time.sleep", lambda s: None)
    from kvnbox.system.metrics import Disk, Metrics

    monkeypatch.setattr(Metrics, "disk", lambda self: Disk(100, 50, 50))
    return env


def test_check_services_transitions(jobs_env: Env) -> None:
    env, sent = jobs_env, Sent()
    units(env, {"caddy": "active", "sing-box": "failed"})
    proc(env, (100, 900), 50)
    msgs = Jobs(env.app, telegram(sent)).check_services()
    assert msgs == ["🔴 <b>Служба sing-box не запущена</b>"]  # mita/netbird нет — пропущены
    assert sent.texts == msgs

    env.runner.responses.clear()
    units(env, {"caddy": "active", "sing-box": "active"})
    proc(env, (200, 1800), 50)
    assert Jobs(env.app, telegram(sent)).check_services() == [
        "🟢 <b>Служба sing-box восстановлена</b>"
    ]


def test_resource_alert_once_and_reset(jobs_env: Env) -> None:
    env, sent = jobs_env, Sent()
    units(env, {})
    proc(env, (100, 900), 95)
    assert Jobs(env.app, telegram(sent)).check_services() == [
        "⚠️ <b>Критическое потребление памяти RAM: 95.0%</b>"
    ]
    proc(env, (200, 1800), 95)
    assert Jobs(env.app, telegram(sent)).check_services() == []  # повторно не шлём
    proc(env, (300, 2700), 85)
    assert Jobs(env.app, telegram(sent)).check_services() == []  # 80..90 — без изменений
    proc(env, (400, 3600), 70)
    assert Jobs(env.app, telegram(sent)).check_services() == [
        "🟢 <b>Потребление памяти RAM нормализовано: 70.0%</b>"
    ]


def test_cpu_average_between_runs(jobs_env: Env) -> None:
    env, sent = jobs_env, Sent()
    units(env, {})
    proc(env, (0, 1000), 10)
    Jobs(env.app, telegram(sent)).check_services()
    proc(env, (950, 1050), 10)  # между запусками: 950 занято из 1000
    assert Jobs(env.app, telegram(sent)).check_services() == [
        "⚠️ <b>Критическая нагрузка на CPU: 95.0%</b>"
    ]


def test_alerts_disabled_still_tracks_state(jobs_env: Env) -> None:
    env, sent = jobs_env, Sent()
    units(env, {"caddy": "failed"})
    proc(env, (1, 9), 10)
    assert Jobs(env.app, telegram(sent, alerts=False)).check_services()
    assert sent.calls == []


def test_check_updates_once_per_version(env: Env) -> None:
    sent = Sent()
    tags = [{"name": "v9.9.9"}, {"name": "v10.0.0-rc1"}, {"name": "v9.10.0"}, {"name": "v1.0.0"}]
    releases = Releases(lambda url, t: json.dumps(tags).encode())
    jobs = Jobs(env.app, telegram(sent), releases)
    assert jobs.check_updates() == "9.10.0"  # semver, не по алфавиту; rc не считается
    assert jobs.check_updates() is None
    assert len(sent.texts) == 1 and __version__ in sent.texts[0]
    assert (env.paths.root / ".update_cache").read_text().strip() == "9.10.0"


def test_check_updates_network_error(env: Env) -> None:
    def fail(url: str, t: float) -> bytes:
        raise OSError("no network")

    assert Jobs(env.app, telegram(Sent()), Releases(fail)).check_updates() is None


def test_backup_job_requirements(env: Env) -> None:
    sent = Sent()
    with pytest.raises(ServiceError, match="Пароль"):
        Jobs(env.app, telegram(sent)).backup()
    with env.app.settings.transaction() as s:
        s.backup.password = "pw"
    with pytest.raises(ServiceError, match="отключены"):
        Jobs(env.app, telegram(sent)).backup()
    unconfigured = Telegram(TelegramSettings(), post=sent)
    with pytest.raises(ServiceError, match="Токен"):
        Jobs(env.app, unconfigured).backup(ignore_enabled=True)


def test_backup_job_sends_and_cleans(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from kvnbox.services.backup import BackupService

    env.app.users.add_connection("a", Protocol.NAIVE)
    with env.app.settings.transaction() as s:
        s.backup.password = "pw"
        s.backup.enabled = True
    made: list[Path] = []

    def fake_create(self: BackupService, password: str | None = None, label: str = "") -> Path:
        p = env.paths.backups_dir / "kvnbox_backup_x_pass.zip"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ZIP")
        made.append(p)
        assert password == "pw"
        return p

    monkeypatch.setattr(BackupService, "create", fake_create)
    sent = Sent()
    assert "отправлена" in Jobs(env.app, telegram(sent)).backup()
    url, body = sent.calls[0]
    assert url.endswith("/bot1:T/sendDocument") and b"ZIP" in body and b'name="chat_id"' in body
    assert not made[0].exists()  # на сервере не хранится


def test_telegram_errors() -> None:
    def bad(url: str, body: bytes, ct: str) -> bytes:
        return b'{"ok": false, "description": "chat not found"}'

    with pytest.raises(NotifyError, match="chat not found"):
        Telegram(TelegramSettings(token="1:T", chat_id="1"), post=bad).send_message("x")
