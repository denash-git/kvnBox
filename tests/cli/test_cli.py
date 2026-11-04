from __future__ import annotations

import io
import json

import pytest

from kvnbox.cli import main
from kvnbox.core.models import Protocol
from kvnbox.system.runner import Result
from tests.conftest import Env


def run(env: Env, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), app=env.app, out=out)
    return code, out.getvalue()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "kvnBox" in capsys.readouterr().out


def test_no_command_prints_help(env: Env) -> None:
    code, text = run(env)
    assert code == 2 and "КОМАНДА" in text
    code, text = run(env, "user")
    assert code == 2 and "ДЕЙСТВИЕ" in text


def test_bad_arguments(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(env, "user", "add", "mama", "wireguard")[0] == 2
    assert "invalid choice" in capsys.readouterr().err


def test_init(env: Env) -> None:
    env.paths.settings_file.unlink()
    env.runner.on("hostname", "-f", result=Result(0, "vps.example.com\n"))
    code, text = run(env, "init", "--domain", "New.Example.org", "--email", "a@example.org")
    assert code == 0, text
    s = env.app.settings.load()
    assert (s.domain, s.admin_email, s.fqdn, s.bbr_enabled) == (
        "new.example.org",
        "a@example.org",
        "vps.example.com",
        True,
    )
    admin = env.app.users.get("admin")
    assert admin is not None
    assert [c.slot for c in admin.connections] == [
        (Protocol.NAIVE, None),
        (Protocol.VLESS, admin.connections[1].transport),
    ]
    assert admin.connections[0].credentials.username == "admin_naive"  # type: ignore[union-attr]
    assert (env.paths.etc / "cron.d" / "kvnbox").exists()
    assert run(env, "init", "--domain", "x.example.org")[0] == 1  # повторно — без --force
    assert run(env, "init", "--domain", "bad domain", "--force")[0] == 1


def test_user_flow(env: Env) -> None:
    code, text = run(env, "user", "add", "mama", "vless-xhttp")
    assert code == 0 and "#mama-xhttp" in text
    assert run(env, "user", "add", "mama", "naive")[0] == 0
    assert run(env, "user", "disable", "mama", "naive")[0] == 0
    code, text = run(env, "user", "list")
    assert "mama: vless-xhttp, naive (выкл)" in text
    code, text = run(env, "user", "links", "mama")
    assert "/sub/" in text and "[выключено]" in text
    assert run(env, "user", "remove", "mama", "naive")[0] == 0
    assert run(env, "user", "remove", "mama")[0] == 0
    code, text = run(env, "user", "links", "mama")
    assert code == 1 and "не найден" in text


def test_apply_failure_exit_code(env: Env) -> None:
    env.runner.on("caddy", "validate", result=Result(1, "", "bad config"))
    code, text = run(env, "apply")
    assert code == 1 and "bad config" in text


def test_status_json(env: Env) -> None:
    code, text = run(env, "status", "--json")
    data = json.loads(text)
    assert code == 0 and data["domain"] == "tst.example.com" and "caddy" in data["services"]


def test_backup_cli(env: Env) -> None:
    run(env, "user", "add", "a", "naive")
    code, text = run(env, "backup", "create")
    assert code == 0 and "kvnbox_backup_" in text
    assert run(env, "backup", "create", "--encrypt")[1].startswith("✗ Пароль")
    code, text = run(env, "backup", "list")
    assert text.count("kvnbox_backup_") == 1
    assert run(env, "backup", "restore")[0] == 0


def test_ssh_and_security(env: Env) -> None:
    env.runner.on("sshd", "-T", result=Result(0, "port 22\npasswordauthentication yes\n"))
    code, text = run(env, "ssh", "status")
    assert code == 0 and "Порт: 22" in text
    code, text = run(env, "ssh", "port", "22")
    assert code == 1 and "уже" in text
    code, text = run(env, "security", "unban", "bad ip!")
    assert code == 1 and "Некорректный" in text


def test_root_required(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import os

    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert main(["status"]) == 1
    assert "root" in capsys.readouterr().err
