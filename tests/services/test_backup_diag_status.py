from __future__ import annotations

import json
import shutil
import zipfile

import pytest

from kvnbox.core.models import Protocol
from kvnbox.services import ServiceError
from kvnbox.services.backup import BackupService, PasswordRequired, is_encrypted
from kvnbox.services.diagnostics import DiagnosticsService
from kvnbox.services.schedule import cron_content
from kvnbox.services.status import StatusService
from kvnbox.system.runner import Result
from tests.services.conftest import Env

# ─── бэкап ───────────────────────────────────────────────────────────────────


def test_backup_roundtrip(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.NAIVE)
    svc = BackupService(env.app)
    path = svc.create()
    assert path.name.startswith("kvnbox_backup_") and not is_encrypted(path)
    assert svc.latest() == path
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == ["settings.json", "users.json"]

    env.app.users.delete_user("mama")
    result = svc.restore(path)
    assert result.ok, result.message
    assert env.app.users.get("mama") is not None
    assert any("pre_restore" in line for line in result.log)
    assert (env.paths.etc / "cron.d" / "kvnbox").exists()


def test_backup_requires_data(env: Env) -> None:
    with pytest.raises(ServiceError, match=r"users\.json"):
        BackupService(env.app).create()


def test_restore_rejects_foreign_and_broken(env: Env, tmp_path: object) -> None:
    from pathlib import Path

    d = Path(str(tmp_path))
    foreign = d / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("users.json", "[]")
        zf.writestr("instance.env", "DOMAIN=x")
    with pytest.raises(ServiceError, match="не бэкап kvnBox"):
        BackupService(env.app).read(foreign)

    broken = d / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("settings.json", '{"domain": "bad domain"}')
        zf.writestr("users.json", "{}")
    with pytest.raises(ServiceError, match="повреждены"):
        BackupService(env.app).read(broken)

    not_zip = d / "x.zip"
    not_zip.write_text("hello")
    with pytest.raises(ServiceError, match="повреждён"):
        BackupService(env.app).read(not_zip)


def test_restore_ignores_paths_inside_archive(env: Env, tmp_path: object) -> None:
    """Имена с каталогами (в т.ч. «../») не распаковываются на диск — читаются по имени."""
    from pathlib import Path

    env.app.users.add_connection("a", Protocol.NAIVE)
    good = BackupService(env.app).create()
    evil = Path(str(tmp_path)) / "evil.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(evil, "w") as dst:
        for name in src.namelist():
            dst.writestr(f"../../{name}", src.read(name))
    _, users = BackupService(env.app).read(evil)
    assert users.users[0].name == "a"
    assert not (env.paths.root.parent.parent / "users.json").exists()


@pytest.mark.skipif(shutil.which("zip") is None, reason="нужна утилита zip (есть на стенде)")
def test_encrypted_backup(env: Env) -> None:
    from kvnbox.system.runner import SubprocessRunner

    env.app.users.add_connection("a", Protocol.NAIVE)
    app = env.app
    app.runner = SubprocessRunner()
    svc = BackupService(app)
    path = svc.create(password="s3cret")
    assert is_encrypted(path) and path.name.endswith("_pass.zip")
    with pytest.raises(PasswordRequired):
        svc.read(path)  # пароль в настройках не задан
    with pytest.raises(PasswordRequired, match="Неверный"):
        svc.read(path, "wrong")
    assert svc.read(path, "s3cret")[1].users[0].name == "a"
    svc.set_password("s3cret")
    assert svc.read(path)[1].users[0].name == "a"  # пароль из настроек


def test_schedule(env: Env) -> None:
    svc = BackupService(env.app)
    svc.set_schedule(True, hour=3, minute=30)
    text = (env.paths.etc / "cron.d" / "kvnbox").read_text()
    assert "30 3 * * * root" in text and "job backup" in text
    svc.set_schedule(False)
    assert "job backup" not in cron_content(env.app.settings.load(), env.paths)
    assert "*/5 * * * * root" in text and "job check-services" in text


# ─── диагностика и статус ────────────────────────────────────────────────────


def test_speedtest_servers(env: Env) -> None:
    env.binaries.add("speedtest")
    payload = {"servers": [{"id": 1234, "name": "ISP", "location": "Moscow", "country": "RU"}]}
    env.runner.on("speedtest", "-L", result=Result(0, json.dumps(payload)))
    (s,) = DiagnosticsService(env.app).speedtest_servers()
    assert (s.id, s.name, s.location) == ("1234", "ISP", "Moscow, RU")
    with pytest.raises(ServiceError):
        DiagnosticsService(env.app).speedtest("12; rm")


def test_speedtest_missing(env: Env) -> None:
    with pytest.raises(ServiceError, match="не установлен"):
        DiagnosticsService(env.app).speedtest()


def test_iperf_start_stop(env: Env) -> None:
    env.binaries.add("iperf3")
    port = DiagnosticsService(env.app).iperf_start()
    calls = env.runner.calls
    assert port == 5201
    assert ["ufw", "allow", "5201/tcp", "comment", "iperf3 TCP"] in calls
    assert any(c[0] == "systemd-run" and "iperf3" in c for c in calls)
    DiagnosticsService(env.app).iperf_stop()
    assert ["systemctl", "stop", "kvnbox-iperf3"] in env.runner.calls


def test_overview(env: Env) -> None:
    env.runner.on("systemctl", "is-active", "caddy", result=Result(0, "active\n"))
    env.runner.on("systemctl", "is-active", "sing-box", result=Result(3, "failed\n"))
    env.runner.on("hostname", "-f", result=Result(0, "vps.example.com\n"))
    (env.paths.root).mkdir(parents=True, exist_ok=True)
    (env.paths.root / ".update_cache").write_text("9.9.9\n")
    ov = StatusService(env.app).overview()
    assert ov.services["caddy"] == "active" and ov.services["sing-box"] == "failed"
    assert ov.services["netbird"] == "not_installed"
    assert ov.fqdn == "vps.example.com" and ov.update_available
