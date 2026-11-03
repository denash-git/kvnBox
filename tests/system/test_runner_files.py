from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kvnbox.cores.base import RenderedFile
from kvnbox.system.files import replace_directory, write_file
from kvnbox.system.runner import (
    NOT_FOUND,
    TIMED_OUT,
    CommandError,
    FakeRunner,
    Result,
    SubprocessRunner,
    check,
)
from kvnbox.system.systemd import Systemd

PY = sys.executable


def test_subprocess_runner_ok() -> None:
    res = SubprocessRunner().run([PY, "-c", "print('hi')"])
    assert res.ok and res.stdout.strip() == "hi"


def test_subprocess_runner_not_found() -> None:
    res = SubprocessRunner().run(["definitely-not-a-command-kvnbox"])
    assert res.returncode == NOT_FOUND and not res.ok


def test_subprocess_runner_timeout() -> None:
    res = SubprocessRunner().run([PY, "-c", "import time; time.sleep(5)"], timeout=0.5)
    assert res.returncode == TIMED_OUT


def test_subprocess_runner_stdin_closed() -> None:
    # без input команда получает EOF, а не висит в ожидании ввода
    res = SubprocessRunner().run(
        [PY, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=10
    )
    assert res.stdout.strip() == "''"


def test_subprocess_runner_input() -> None:
    res = SubprocessRunner().run(
        [PY, "-c", "import sys; print(sys.stdin.read().upper())"], input="y"
    )
    assert res.stdout.strip() == "Y"


def test_check_raises() -> None:
    fake = FakeRunner(default=Result(3, "", "boom"))
    with pytest.raises(CommandError, match="boom"):
        check(fake, ["x"])


def test_fake_runner_prefix_and_log() -> None:
    fake = FakeRunner().on("systemctl", "is-active", result=Result(3))
    assert not fake.run(["systemctl", "is-active", "--quiet", "caddy"]).ok
    assert fake.run(["systemctl", "restart", "caddy"]).ok
    assert fake.called("systemctl", "restart")
    assert len(fake.calls) == 2


# ─── systemd ─────────────────────────────────────────────────────────────────


def test_reload_or_restart_active_reloads() -> None:
    fake = FakeRunner()
    Systemd(fake).reload_or_restart("caddy")
    assert ["systemctl", "reload", "caddy"] in fake.calls
    assert not fake.called("systemctl", "restart")


def test_reload_or_restart_failed_reload_restarts_clean() -> None:
    fake = FakeRunner().on("systemctl", "reload", result=Result(1))
    Systemd(fake).reload_or_restart("caddy")
    assert fake.calls[-2:] == [
        ["systemctl", "reset-failed", "caddy"],
        ["systemctl", "restart", "caddy"],
    ]


def test_reload_or_restart_inactive_restarts() -> None:
    fake = FakeRunner().on("systemctl", "is-active", result=Result(3))
    Systemd(fake).reload_or_restart("caddy")
    assert not fake.called("systemctl", "reload")
    assert fake.called("systemctl", "restart", "caddy")


def test_state_and_journal() -> None:
    fake = (
        FakeRunner()
        .on("systemctl", "is-active", result=Result(3, "failed\n"))
        .on("journalctl", result=Result(0, "line1\n"))
    )
    sd = Systemd(fake)
    assert sd.state("x") == "failed"
    assert sd.journal("caddy", 5) == "line1\n"
    assert ["journalctl", "-u", "caddy", "-n", "5", "--no-pager", "-o", "short-iso"] in fake.calls


# ─── файлы ───────────────────────────────────────────────────────────────────


def test_write_file(tmp_path: Path) -> None:
    f = RenderedFile(tmp_path / "a" / "b.conf", "x\n", 0o640, owner="nobody-kvnbox")
    write_file(f)  # несуществующий владелец — без ошибки
    assert f.path.read_text() == "x\n"


def test_replace_directory(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "old").write_text("old")
    replace_directory(sub, [RenderedFile(sub / "t1", "A", 0o644), RenderedFile(sub / "t2", "B")])
    assert sorted(p.name for p in sub.iterdir()) == ["t1", "t2"]
    assert (sub / "t1").read_text() == "A"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sub"]  # без мусора


def test_replace_directory_empty_and_missing(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    replace_directory(sub, [])
    assert sub.is_dir() and not any(sub.iterdir())


def test_replace_directory_rejects_foreign_path(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    with pytest.raises(ValueError):
        replace_directory(sub, [RenderedFile(tmp_path / "other" / "x", "")])
    assert not sub.exists()
    assert list(tmp_path.iterdir()) == []
