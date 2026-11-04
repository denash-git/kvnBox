"""Обновление панели (D-039), исходные правила фаервола, файлы установщика."""

from __future__ import annotations

import io
import os
import re
import tarfile
from pathlib import Path

import pytest

from kvnbox.services import ServiceError
from kvnbox.services.firewall import FirewallService, session_ssh_port
from kvnbox.services.update import UpdateService, archive_url
from kvnbox.system.releases import Releases
from kvnbox.system.runner import Result
from tests.services.conftest import Env

REPO = Path(__file__).resolve().parents[2]
posix = pytest.mark.skipif(os.name != "posix", reason="символьные ссылки — на Linux")


# ─── обновление ──────────────────────────────────────────────────────────────


def _archive(version: str, top: str = "kvnBox-x") -> bytes:
    buf = io.BytesIO()
    files = {
        f"{top}/src/kvnbox/__init__.py": f'"""kvnBox."""\n\n__version__ = "{version}"\n',
        f"{top}/requirements.lock": "pydantic==2.0 --hash=sha256:00\n",
    }
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _installed(env: Env, version: str = "0.1.0") -> Path:
    """Раскладка, как после install.sh: releases/<ver>, ссылка app, data/."""
    p = env.paths
    old = p.releases_dir / version
    (old / "src" / "kvnbox").mkdir(parents=True)
    p.app_link.symlink_to(old)
    return old


def _fetcher(archives: dict[str, bytes]):  # type: ignore[no-untyped-def]
    def fetch(url: str, timeout: float) -> bytes:
        return archives[url]

    return fetch


@posix
def test_update_switches_release_and_keeps_previous(env: Env) -> None:
    old = _installed(env)
    (env.paths.releases_dir / "0.0.9").mkdir()  # позапрошлая — удаляется
    (env.paths.root / ".update_cache").write_text("0.2.0\n")
    svc = UpdateService(env.app, Releases(_fetcher({archive_url("0.2.0"): _archive("0.2.0")})))
    assert svc.update("0.2.0") == "Обновление до версии 0.2.0 успешно завершено!"
    p = env.paths
    assert p.app_link.resolve() == (p.releases_dir / "0.2.0").resolve()
    assert sorted(d.name for d in p.releases_dir.iterdir()) == ["0.1.0", "0.2.0"]
    assert old.exists()
    pip = env.runner.calls[0]
    assert pip[0].endswith(".venv/bin/pip") and "--require-hashes" in pip
    assert env.runner.calls[1][-3:] == ["-m", "kvnbox", "apply"]
    assert not (p.root / ".update_cache").exists()


@posix
def test_update_rolls_back_when_new_version_fails(env: Env) -> None:
    old = _installed(env)
    users_before = env.paths.settings_file.read_text(encoding="utf-8")
    applies: list[str] = []

    def apply(argv: list[str], _: str | None) -> Result:
        applies.append(os.readlink(env.paths.app_link))
        if len(applies) == 1:  # новая версия портит данные и падает
            env.paths.settings_file.write_text("{broken")
            return Result(1, stderr="ValidationError")
        return Result(0)

    env.runner.on(str(env.paths.venv / "bin" / "python"), result=apply)
    svc = UpdateService(env.app, Releases(_fetcher({archive_url("0.2.0"): _archive("0.2.0")})))
    with pytest.raises(ServiceError, match=r"возвращена прежняя \(0\.1\.0\)"):
        svc.update("0.2.0")
    assert env.paths.app_link.resolve() == old.resolve()
    assert env.paths.settings_file.read_text() == users_before
    assert len(applies) == 2  # конфиги пересобраны старой версией
    assert not (env.paths.releases_dir / "0.2.0").exists()  # сломанная версия удалена


@posix
def test_update_rejects_wrong_archive(env: Env) -> None:
    old = _installed(env)
    svc = UpdateService(env.app, Releases(_fetcher({archive_url("0.2.0"): _archive("0.1.5")})))
    with pytest.raises(ServiceError, match=r"не содержит kvnBox 0.2.0"):
        svc.update("0.2.0")
    assert env.paths.app_link.resolve() == old.resolve()
    assert env.runner.calls == []


@posix
def test_update_pip_failure_keeps_current(env: Env) -> None:
    old = _installed(env)
    env.runner.on(str(env.paths.venv / "bin" / "pip"), result=Result(1, stderr="hash mismatch"))
    svc = UpdateService(env.app, Releases(_fetcher({archive_url("0.2.0"): _archive("0.2.0")})))
    with pytest.raises(ServiceError, match="hash mismatch"):
        svc.update("0.2.0")
    assert env.paths.app_link.resolve() == old.resolve()
    assert not (env.paths.releases_dir / "0.2.0").exists()


def test_update_requires_installer_layout(env: Env) -> None:
    with pytest.raises(ServiceError, match=r"install.sh"):
        UpdateService(env.app).update("0.2.0")
    with pytest.raises(ServiceError, match="Неверный номер"):
        UpdateService(env.app).update("../../etc")


def test_restart_bot_only_when_running(env: Env) -> None:
    env.runner.on("systemctl", "is-active", result=Result(3, "inactive\n"))
    UpdateService(env.app).restart_bot()
    assert not env.runner.called("systemctl", "--no-block", "restart")


# ─── фаервол ─────────────────────────────────────────────────────────────────


def test_session_ssh_port() -> None:
    assert session_ssh_port({"SSH_CONNECTION": "1.2.3.4 50000 5.6.7.8 2222"}) == 2222
    assert session_ssh_port({}) is None
    assert session_ssh_port({"SSH_CONNECTION": "garbage"}) is None


def test_firewall_setup(env: Env) -> None:
    env.runner.on("sshd", "-T", result=Result(0, "port 2222\n"))
    msg = FirewallService(env.app).setup({"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"})
    ufw = [c[1:] for c in env.runner.calls if c[0] == "ufw"]
    assert ufw == [
        ["--force", "reset"],
        ["default", "deny", "incoming"],
        ["default", "allow", "outgoing"],
        ["allow", "22/tcp", "comment", "SSH Port"],  # порт текущей сессии — не отрезать себя
        ["allow", "2222/tcp", "comment", "SSH Port"],
        ["allow", "443/tcp"],
        ["allow", "443/udp"],
        ["--force", "enable"],
    ]
    assert "22/tcp, 2222/tcp, 443/tcp" in msg


def test_firewall_not_enabled_if_rules_failed(env: Env) -> None:
    env.runner.on("ufw", "allow", result=Result(1, stderr="ERROR: bad port"))
    with pytest.raises(ServiceError, match="bad port"):
        FirewallService(env.app).setup({})
    assert not env.runner.called("ufw", "--force", "enable")


# ─── файлы установщика ───────────────────────────────────────────────────────


def test_requirements_lock_has_hashes_for_all_deps() -> None:
    lock = (REPO / "requirements.lock").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[a-z])", lock.split("\n", 2)[2])
    names = {b.split("==")[0] for b in blocks}
    assert {"pydantic", "aiogram", "pydantic-core", "aiohttp"} <= names
    assert all("--hash=sha256:" in b for b in blocks)
    for dep in re.findall(
        r'"([a-z-]+)>=',
        (REPO / "pyproject.toml").read_text(encoding="utf-8").split("[project.scripts]")[0],
    ):
        assert dep in names


def test_versions_env_complete() -> None:
    env = dict(
        line.split("=", 1)
        for line in (REPO / "versions.env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    for name in ("SINGBOX", "MITA", "GO"):
        for arch in ("AMD64", "ARM64"):
            assert re.fullmatch(r"[0-9a-f]{64}", env[f"{name}_SHA256_{arch}"])
    assert env["FORWARDPROXY_MODULE"].startswith("github.com/klzgrad/forwardproxy@")
    script = (REPO / "install.sh").read_text(encoding="utf-8")
    for key in env:
        if not key.endswith(("_AMD64", "_ARM64")):
            assert key in script, f"{key} не используется установщиком"


def test_install_steps_all_defined() -> None:
    script = (REPO / "install.sh").read_text(encoding="utf-8")
    steps = re.findall(r"^\$?\w*\s*\|*\s*add_step (\w+)", script, re.M)
    steps += re.findall(r"\|\| add_step (\w+)", script)
    assert len(steps) >= 20
    for fn in steps:
        assert re.search(rf"^{fn}\(\) \{{", script, re.M), fn
