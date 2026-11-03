from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError

from kvnbox.clients.subscription import build_subscriptions
from kvnbox.core.models import (
    Connection,
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UuidCredentials,
)
from kvnbox.core.paths import Paths
from kvnbox.cores import caddy, mita, singbox
from kvnbox.cores.base import active_connections, fill_template, load_asset
from kvnbox.render import render_all


def naive(name: str, enabled: bool = True) -> Connection:
    return Connection(
        protocol=Protocol.NAIVE,
        core=CoreKind.CADDY,
        enabled=enabled,
        credentials=PasswordCredentials(username=f"u_{name}", password="p" * 24),
    )


def vless(transport: Transport, enabled: bool = True) -> Connection:
    return Connection(
        protocol=Protocol.VLESS,
        transport=transport,
        core=CoreKind.SINGBOX,
        enabled=enabled,
        credentials=UuidCredentials(),
    )


# ─── шаблоны ─────────────────────────────────────────────────────────────────


def test_fill_template_single_pass() -> None:
    # подставленное значение с {{...}} не раскрывается повторно
    assert fill_template("a {{X}} b", {"X": "{{Y}}"}) == "a {{Y}} b"


def test_fill_template_missing_value() -> None:
    with pytest.raises(KeyError, match="NAME"):
        fill_template("{{NAME}}", {})


def test_fill_template_unused_value() -> None:
    with pytest.raises(KeyError, match="EXTRA"):
        fill_template("x", {"EXTRA": "1"})


def test_caddy_template_is_packaged() -> None:
    assert "{{FORWARD_PROXY_BLOCK}}" in load_asset("Caddyfile.tmpl")


# ─── выбор подключений по ядру ───────────────────────────────────────────────


def test_active_connections_filters_core_and_enabled() -> None:
    a = User(name="a", connections=[naive("a"), vless(Transport.WS)])
    b = User(name="b", connections=[naive("b", enabled=False)])
    got = [c for _, c in active_connections([a, b], CoreKind.CADDY)]
    assert got == [a.connections[0]]


# ─── Caddy ───────────────────────────────────────────────────────────────────


def test_caddy_without_naive_users(settings: Settings, paths: Paths) -> None:
    text = caddy.render_caddyfile(
        settings, [User(name="a", connections=[naive("a", False)])], paths
    )
    assert "# No NaiveProxy users configured" in text
    assert "forward_proxy {" not in text


def test_caddy_email(settings: Settings, paths: Paths) -> None:
    assert "# anonymous ACME" in caddy.render_caddyfile(settings, [], paths)
    settings.admin_email = "me@example.org"
    assert "email me@example.org" in caddy.render_caddyfile(settings, [], paths)


# ─── sing-box ────────────────────────────────────────────────────────────────


def test_singbox_users_split_by_transport(settings: Settings) -> None:
    u = User(
        name="mama",
        connections=[vless(Transport.WS), vless(Transport.GRPC, enabled=False)],
    )
    cfg = singbox.build_config(settings, [u])
    by_tag = {i["tag"]: i for i in cfg["inbounds"]}
    assert by_tag["vless-ws-in"]["users"] == [
        {"uuid": u.connections[0].credentials.uuid, "name": "mama@vless"}  # type: ignore[union-attr]
    ]
    assert by_tag["vless-grpc-in"]["users"] == []  # отключённое не попадает
    assert by_tag["vless-xhttp-in"]["transport"]["type"] == "httpupgrade"


def test_singbox_file_permissions(settings: Settings, paths: Paths) -> None:
    (f,) = singbox.render(settings, [], paths)
    assert (f.mode, f.owner, f.group) == (0o640, "root", "sing-box")
    json.loads(f.content)


# ─── mita ────────────────────────────────────────────────────────────────────


def test_mita_disabled_renders_nothing(settings: Settings, paths: Paths) -> None:
    assert mita.render(settings, [], paths) == []


def test_mita_enabled_without_users_is_valid(settings: Settings, paths: Paths) -> None:
    settings.mieru.enabled = True
    files = mita.render(settings, [], paths)
    assert {f.path for f in files} == {paths.mita_config, paths.mita_service_override}
    cfg = json.loads(next(f for f in files if f.path == paths.mita_config).content)
    assert cfg["users"] == []


# ─── подписки и общий рендер ─────────────────────────────────────────────────


def test_subscription_skips_fully_disabled_user(settings: Settings) -> None:
    on = User(name="on", connections=[naive("on"), vless(Transport.GRPC, enabled=False)])
    off = User(name="off", connections=[vless(Transport.WS, enabled=False)])
    subs = build_subscriptions([on, off], settings)
    assert list(subs) == [on.sub_token]
    decoded = base64.b64decode(subs[on.sub_token]).decode()
    assert decoded.count("\n") == 0 and decoded.startswith("naive://u_on:")


def test_render_all_paths(settings: Settings, paths: Paths) -> None:
    settings.mieru.enabled = True
    u = User(name="a", connections=[naive("a")])
    plan = render_all(settings, [u], paths)
    assert [f.path for f in plan.files] == [
        paths.caddy_config,
        paths.singbox_config,
        paths.mita_service_override,
        paths.mita_config,
    ]
    assert [f.path for f in plan.subscriptions] == [paths.sub_dir / u.sub_token]
    assert plan.subscriptions[0].mode == 0o644


# ─── валидация того, что попадает в конфиги ──────────────────────────────────


@pytest.mark.parametrize("bad", ["a b", "a{b", "a}b", "a\nb", "a:b", "a/b", "a#b", 'a"b'])
def test_password_credentials_reject_unsafe(bad: str) -> None:
    with pytest.raises(ValidationError):
        PasswordCredentials(username=bad, password="ok")
    with pytest.raises(ValidationError):
        PasswordCredentials(username="ok", password=bad)


@pytest.mark.parametrize("bad", ["/a b", "/a{b}", "/a\n", "/a;b"])
def test_paths_reject_unsafe(settings: Settings, bad: str) -> None:
    with pytest.raises(ValidationError):
        settings.vless.ws_path = bad


@pytest.mark.parametrize("bad", ["x", "a@b", "a b@c.com", "a@b.c\nd"])
def test_email_validated(settings: Settings, bad: str) -> None:
    with pytest.raises(ValidationError):
        settings.admin_email = bad


def test_fakesite_assets_present() -> None:
    from importlib import resources

    root = resources.files("kvnbox.assets").joinpath("fakesite")
    for template in ("aether", "chronos", "nexus", "stratum", "synapse"):
        assert root.joinpath(template, "index.html").is_file()


def test_mieru_client_json_ip_vs_domain(settings: Settings) -> None:
    from kvnbox.clients.links import mieru_client_json

    u = User(
        name="m",
        connections=[
            Connection(
                protocol=Protocol.MIERU,
                core=CoreKind.MITA,
                credentials=PasswordCredentials(username="u", password="p"),
            )
        ],
    )
    server = json.loads(mieru_client_json(u, u.connections[0], settings))["servers"][0]
    assert server["domainName"] == settings.domain and "ipAddress" not in server
