from __future__ import annotations

import json

import pytest

from kvnbox.core.models import Protocol
from kvnbox.services import ServiceError
from kvnbox.services.modules import BbrModule, MieruModule, NetbirdModule, SingboxModule
from kvnbox.services.security import SecurityService
from kvnbox.services.ssh import SshService
from kvnbox.services.users import UserService
from kvnbox.system.releases import Releases
from kvnbox.system.runner import Result
from tests.services.conftest import Env


def fake_releases(tag: str = "v3.40.0") -> tuple[Releases, list[str]]:
    fetched: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        fetched.append(url)
        if "api.github.com" in url:
            return json.dumps({"tag_name": tag}).encode()
        return b"DEB"

    return Releases(fetch), fetched


def with_arch(env: Env) -> None:
    env.runner.on("dpkg", "--print-architecture", result=Result(0, "amd64\n"))


# ─── модули ──────────────────────────────────────────────────────────────────


def test_mieru_install_does_not_enable(env: Env) -> None:
    with_arch(env)
    releases, fetched = fake_releases()
    result = MieruModule(env.app, releases=releases).install()
    assert result.ok
    assert fetched[-1].endswith("/v3.40.0/mita_3.40.0_amd64.deb")
    assert env.runner.called("dpkg", "-i")
    assert not env.app.settings.load().mieru.enabled  # включается отдельно (1:1)


def test_mieru_port_and_uninstall(env: Env) -> None:
    mod = MieruModule(env.app)
    assert mod.set_port(30121).ok
    assert env.app.settings.load().mieru.port == 30121
    with pytest.raises(ServiceError):
        mod.set_port(0)
    env.binaries.discard("mita")
    assert mod.uninstall().ok
    assert env.runner.called("dpkg", "-P", "mita")
    assert not env.app.settings.load().mieru.enabled


def test_mieru_update_requires_install(env: Env) -> None:
    env.binaries.discard("mita")
    with pytest.raises(ServiceError, match="не установлен"):
        MieruModule(env.app).update()


def test_singbox_update(env: Env) -> None:
    with_arch(env)
    releases, fetched = fake_releases("v1.14.5")
    result = SingboxModule(env.app, releases=releases).update()
    assert result.ok and result.message.startswith("sing-box 1.14.5")
    assert fetched[-1].endswith("/v1.14.5/sing-box_1.14.5_linux_amd64.deb")


def test_unsupported_arch(env: Env) -> None:
    env.runner.on("dpkg", "--print-architecture", result=Result(0, "i386\n"))
    releases, _ = fake_releases()
    with pytest.raises(ServiceError, match="архитектура"):
        SingboxModule(env.app, releases=releases).update()


def test_singbox_info(env: Env) -> None:
    env.runner.on("sing-box", "version", result=Result(0, "sing-box version 1.14.4\n\nEnv: go"))
    assert SingboxModule(env.app).info().version == "1.14.4"


def test_netbird(env: Env) -> None:
    nb = NetbirdModule(env.app)
    with pytest.raises(ServiceError, match="Setup Key"):
        nb.up()
    with pytest.raises(ServiceError):
        nb.set_setup_key("bad key; rm")
    nb.set_setup_key("ABCD-1234")
    nb.set_management_url("https://nb.example.com/")
    nb.up()
    assert env.runner.calls[-1] == [
        "netbird",
        "up",
        "--setup-key",
        "ABCD-1234",
        "--management-url",
        "https://nb.example.com",
    ]
    assert nb.status().state == "not_installed"
    env.binaries.add("netbird")
    env.runner.on(
        "netbird",
        "status",
        result=Result(0, "NetBird IP: 100.64.0.5/16\nPeers count: 2/3 Connected\n"),
    )
    st = nb.status()
    assert (st.ip, st.peers) == ("100.64.0.5/16", "2/3 Connected")
    nb.uninstall()
    assert env.app.settings.load().netbird.setup_key == ""


def test_bbr_toggle(env: Env) -> None:
    d = env.paths.proc / "sys" / "net" / "ipv4"
    d.mkdir(parents=True)
    (d / "tcp_congestion_control").write_text("cubic\n")
    (d / "tcp_available_congestion_control").write_text("cubic bbr\n")
    assert "включена" in BbrModule(env.app).toggle()
    assert env.app.settings.load().bbr_enabled
    (d / "tcp_congestion_control").write_text("bbr\n")
    assert "отключена" in BbrModule(env.app).toggle()
    assert not env.app.settings.load().bbr_enabled


# ─── защита ──────────────────────────────────────────────────────────────────


def test_security_enable_uses_real_ssh_port(env: Env) -> None:
    env.runner.on("sshd", "-T", result=Result(0, "port 2222\npasswordauthentication yes\n"))
    SecurityService(env.app).enable()
    assert "port = 2222" in env.paths.fail2ban_jail.read_text()
    assert env.app.settings.load().security.scanner_protection


def test_unban_current_session(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CLIENT", "198.51.100.7 51234 22")
    SecurityService(env.app).unban_current_session()
    assert env.runner.calls[-1] == ["fail2ban-client", "unban", "198.51.100.7"]
    monkeypatch.delenv("SSH_CLIENT")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    with pytest.raises(ServiceError):
        SecurityService(env.app).unban_current_session()


# ─── SSH ─────────────────────────────────────────────────────────────────────


def test_change_port_order(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sshd = env.app.sshd
    order: list[str] = []
    app = env.app
    monkeypatch.setattr(type(sshd), "port", lambda self: 22)
    monkeypatch.setattr(type(sshd), "set_port", lambda self, p: order.append(f"sshd {p}"))
    monkeypatch.setattr(
        type(app.fail2ban), "update_ssh_port", lambda self, p: order.append(f"f2b {p}")
    )
    svc = SshService(app)
    assert svc.change_port(2222) == 22
    allow = env.runner.calls.index(["ufw", "allow", "2222/tcp", "comment", "SSH Port"])
    assert allow == 0 and order == ["sshd 2222", "f2b 2222"]
    with pytest.raises(ServiceError, match="уже"):
        svc.change_port(22)
    svc.confirm_port_change(22)  # 22 остаётся
    assert not env.runner.called("ufw", "delete", "allow", "22/tcp")
    svc.confirm_port_change(2200)
    assert env.runner.calls[-1] == ["ufw", "delete", "allow", "2200/tcp"]


def test_change_port_failure_closes_new_port(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from kvnbox.system.sshd import SshError

    def fail(self: object, p: int) -> None:
        raise SshError("не запустился")

    monkeypatch.setattr(type(env.app.sshd), "port", lambda self: 22)
    monkeypatch.setattr(type(env.app.sshd), "set_port", fail)
    with pytest.raises(ServiceError, match="не запустился"):
        SshService(env.app).change_port(2222)
    assert env.runner.calls[-1] == ["ufw", "delete", "allow", "2222/tcp"]


def test_users_service_survives_bad_name_without_apply(env: Env) -> None:
    with pytest.raises(ServiceError):
        UserService(env.app).create("bad:name", Protocol.NAIVE)
    assert not env.runner.called("caddy")
