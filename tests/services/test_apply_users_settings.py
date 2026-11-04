from __future__ import annotations

import pytest

from kvnbox.core.models import Protocol, Transport
from kvnbox.services import ServiceError
from kvnbox.services.apply import Applier
from kvnbox.services.settings import SettingsService
from kvnbox.services.users import UserService, slot_label
from kvnbox.system.runner import Result
from tests.services.conftest import Env

# ─── применение конфигурации ─────────────────────────────────────────────────


def test_apply_success(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.NAIVE)
    result = Applier(env.app).apply()
    assert result.ok, result.message
    p, calls = env.paths, env.runner.calls
    assert "basic_auth" in p.caddy_config.read_text()
    assert p.singbox_config.exists()
    assert len(list(p.sub_dir.iterdir())) == 1
    assert (p.fakesite_dir / "aether" / "index.html").exists()
    # проверка во временных файлах — до записи рабочих
    validate = next(c for c in calls if c[:2] == ["caddy", "validate"])
    assert ".staging" in validate[-1]
    assert next(c for c in calls if c[0] == "sing-box")[:3] == ["sing-box", "check", "-c"]
    assert ["systemctl", "reload", "caddy"] in calls
    assert ["systemctl", "restart", "sing-box"] in calls
    assert not (p.root / ".staging").exists()
    # Mieru выключен, mita установлен → остановлен
    assert ["systemctl", "disable", "mita"] in calls
    assert ["ufw", "delete", "allow", "80/tcp"] not in calls  # 80 удаляется по номерам правил


def test_apply_validation_error_changes_nothing(env: Env) -> None:
    env.runner.on("sing-box", "check", result=Result(1, "", "unknown field"))
    result = Applier(env.app).apply()
    assert not result.ok and "unknown field" in result.message
    assert not env.paths.caddy_config.exists() and not env.paths.singbox_config.exists()
    assert not env.runner.called("systemctl", "restart")


def test_apply_restart_failure_rolls_back(env: Env) -> None:
    env.paths.caddy_config.parent.mkdir(parents=True)
    env.paths.caddy_config.write_text("OLD CADDY")
    env.app.users.add_connection("u", Protocol.NAIVE)
    env.runner.on("systemctl", "restart", "sing-box", result=Result(1, "", "boom"))
    result = Applier(env.app).apply()
    assert not result.ok and "boom" in result.message and "восстановлена" in result.message
    assert env.paths.caddy_config.read_text() == "OLD CADDY"
    assert not env.paths.singbox_config.exists()  # раньше не было — удалён
    assert not env.paths.sub_dir.exists()  # подписки не трогали


def test_apply_mieru_enabled(env: Env) -> None:
    with env.app.settings.transaction() as s:
        s.mieru.enabled = True
        s.mieru.port = 30121
    env.app.users.add_connection("u", Protocol.MIERU)
    assert Applier(env.app).apply().ok
    calls = env.runner.calls
    assert env.paths.mita_config.exists() and env.paths.mita_service_override.exists()
    assert ["systemctl", "daemon-reload"] in calls  # override новый
    assert ["systemctl", "enable", "mita"] in calls
    assert ["ufw", "allow", "30121/tcp", "comment", "Mieru TCP"] in calls
    assert ["ufw", "allow", "30121/udp", "comment", "Mieru UDP"] in calls


def test_apply_mieru_enabled_but_not_installed(env: Env) -> None:
    env.binaries.discard("mita")
    with env.app.settings.transaction() as s:
        s.mieru.enabled = True
    result = Applier(env.app).apply()
    assert result.ok and any("не установлен" in line for line in result.log)
    assert not env.runner.called("systemctl", "enable", "mita")


# ─── пользователи ────────────────────────────────────────────────────────────


def test_create_user_and_links(env: Env) -> None:
    svc = UserService(env.app)
    conn, result = svc.create("mama", Protocol.VLESS, Transport.HTTPUPGRADE)
    assert result.ok
    user = svc.get("mama")
    assert slot_label(conn) == "VLESS over XHTTP"
    assert svc.link(user, conn).endswith("#mama-xhttp")
    assert svc.subscription_url(user) == f"https://tst.example.com/sub/{user.sub_token}"
    with pytest.raises(ServiceError, match="уже существует"):
        svc.create("mama", Protocol.NAIVE)


def test_user_change_reverted_when_apply_fails(env: Env) -> None:
    svc = UserService(env.app)
    svc.create("mama", Protocol.NAIVE)
    env.runner.on("caddy", "validate", result=Result(1, "", "bad"))
    _, result = svc.add_connection("mama", Protocol.MIERU)
    assert not result.ok
    assert [c.protocol for c in svc.get("mama").connections] == [Protocol.NAIVE]


def test_user_operations(env: Env) -> None:
    svc = UserService(env.app)
    svc.create("a", Protocol.NAIVE)
    svc.add_connection("a", Protocol.VLESS, Transport.GRPC)
    assert svc.set_enabled("a", False, Protocol.NAIVE).ok
    assert [c.enabled for c in svc.get("a").connections] == [False, True]
    assert svc.remove_connection("a", Protocol.VLESS, Transport.GRPC).ok
    assert svc.delete("a").ok
    assert svc.all() == []
    with pytest.raises(ServiceError, match="не найден"):
        svc.get("a")
    with pytest.raises(ServiceError):
        svc.create("bad name", Protocol.NAIVE)


def test_mieru_configs_and_qr(env: Env) -> None:
    svc = UserService(env.app)
    conn, _ = svc.create("m", Protocol.MIERU)
    cfg = svc.mieru_configs(svc.get("m"), conn)
    assert set(cfg) == {"json", "clash", "singbox"} and "domainName" in cfg["json"]
    env.runner.on("qrencode", result=Result(0, "█▀▀█\n"))
    assert svc.qr_terminal("x") == "█▀▀█\n"


# ─── настройки ───────────────────────────────────────────────────────────────


def test_settings_domain_email(env: Env) -> None:
    svc = SettingsService(env.app)
    assert svc.set_domain_email("new.example.org", "me@example.org").ok
    assert svc.get().domain == "new.example.org"
    with pytest.raises(ServiceError, match="домен"):
        svc.set_domain_email("bad domain", "")
    assert svc.get().domain == "new.example.org"


def test_settings_reverted_when_apply_fails(env: Env) -> None:
    env.runner.on("caddy", "validate", result=Result(1, "", "bad"))
    svc = SettingsService(env.app)
    assert not svc.set_vless_paths(ws_path="/ws-new").ok
    assert svc.get().vless.ws_path == "/"


def test_settings_template_and_paths(env: Env) -> None:
    svc = SettingsService(env.app)
    assert svc.set_site_template("nexus").ok
    with pytest.raises(ServiceError):
        svc.set_site_template("nope")
    assert svc.set_vless_paths(grpc_service="svc", httpupgrade_path="/up").ok
    v = svc.get().vless
    assert (v.ws_path, v.grpc_service, v.httpupgrade_path) == ("/", "svc", "/up")
    with pytest.raises(ServiceError, match="путь"):
        svc.set_vless_paths(ws_path="/a b")


def test_set_fqdn_updates_hosts(env: Env) -> None:
    hosts = env.paths.etc / "hosts"
    hosts.parent.mkdir(parents=True, exist_ok=True)
    hosts.write_text("127.0.0.1 localhost\n127.0.1.1 old-host old-host-2\n")
    env.runner.on("hostname", result=Result(0, "old-host\n"))
    SettingsService(env.app).set_fqdn("vps.example.org")
    assert hosts.read_text() == "127.0.0.1 localhost\n127.0.1.1 vps.example.org old-host-2\n"
    assert ["hostnamectl", "set-hostname", "vps.example.org"] in env.runner.calls
    assert env.app.settings.load().fqdn == "vps.example.org"
    with pytest.raises(ServiceError):
        SettingsService(env.app).set_fqdn("bad_name!")
