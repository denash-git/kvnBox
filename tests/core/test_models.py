from __future__ import annotations

import pytest
from pydantic import ValidationError

from kvnbox.core.models import (
    Connection,
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UsersDB,
    UuidCredentials,
    default_core,
    supported_cores,
)


def vless(transport: Transport = Transport.WS, uuid: str | None = None) -> Connection:
    creds = UuidCredentials(uuid=uuid) if uuid else UuidCredentials()
    return Connection(
        protocol=Protocol.VLESS, transport=transport, core=CoreKind.SINGBOX, credentials=creds
    )


def naive(username: str = "u1", password: str = "p1") -> Connection:
    return Connection(
        protocol=Protocol.NAIVE,
        core=CoreKind.CADDY,
        credentials=PasswordCredentials(username=username, password=password),
    )


# ─── ядра ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("protocol", "transport", "core"),
    [
        (Protocol.VLESS, Transport.WS, CoreKind.SINGBOX),
        (Protocol.VLESS, Transport.GRPC, CoreKind.SINGBOX),
        (Protocol.VLESS, Transport.HTTPUPGRADE, CoreKind.SINGBOX),
        (Protocol.NAIVE, None, CoreKind.CADDY),
        (Protocol.MIERU, None, CoreKind.MITA),
    ],
)
def test_default_cores(protocol: Protocol, transport: Transport | None, core: CoreKind) -> None:
    assert default_core(protocol, transport) is core
    assert core in supported_cores(protocol, transport)


def test_no_core_for_invalid_slot() -> None:
    with pytest.raises(ValueError):
        default_core(Protocol.NAIVE, Transport.WS)


# ─── подключения ─────────────────────────────────────────────────────────────


def test_vless_requires_transport() -> None:
    with pytest.raises(ValidationError, match="транспорт"):
        Connection(protocol=Protocol.VLESS, core=CoreKind.SINGBOX, credentials=UuidCredentials())


def test_naive_rejects_transport() -> None:
    with pytest.raises(ValidationError):
        Connection(
            protocol=Protocol.NAIVE,
            transport=Transport.WS,
            core=CoreKind.CADDY,
            credentials=PasswordCredentials(username="a", password="b"),
        )


def test_core_must_support_protocol() -> None:
    with pytest.raises(ValidationError, match="ядро"):
        Connection(
            protocol=Protocol.NAIVE,
            core=CoreKind.SINGBOX,
            credentials=PasswordCredentials(username="a", password="b"),
        )


def test_credentials_kind_must_match() -> None:
    with pytest.raises(ValidationError, match="учётных"):
        Connection(
            protocol=Protocol.VLESS,
            transport=Transport.WS,
            core=CoreKind.SINGBOX,
            credentials=PasswordCredentials(username="a", password="b"),
        )


def test_invalid_uuid() -> None:
    with pytest.raises(ValidationError):
        UuidCredentials(uuid="nope")


def test_connection_json_roundtrip() -> None:
    c = vless(Transport.GRPC)
    assert Connection.model_validate_json(c.model_dump_json()) == c


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"domain": "a.example.com", "typo_field": 1})


# ─── пользователи ────────────────────────────────────────────────────────────


def test_user_active_if_any_enabled() -> None:
    a, b = vless(), naive()
    b.enabled = False
    assert User(name="u", connections=[a, b]).active
    a.enabled = False
    assert not User(name="u", connections=[a, b]).active


def test_user_duplicate_slot_rejected() -> None:
    with pytest.raises(ValidationError, match="повторяются"):
        User(name="u", connections=[vless(), vless()])


def test_user_name_without_spaces_or_colon() -> None:
    for bad in ("a b", "a:b", ""):
        with pytest.raises(ValidationError):
            User(name=bad)


def test_user_find() -> None:
    u = User(name="u", connections=[vless(Transport.GRPC), naive()])
    assert u.find(Protocol.NAIVE) is u.connections[1]
    assert u.find(Protocol.VLESS, Transport.WS) is None


def test_db_unique_names_tokens_uuids_logins() -> None:
    with pytest.raises(ValidationError, match="имя"):
        UsersDB(users=[User(name="a"), User(name="a")])
    with pytest.raises(ValidationError, match="токен"):
        UsersDB(users=[User(name="a", sub_token="t" * 16), User(name="b", sub_token="t" * 16)])
    same = "6f1c2a8e-3f4b-4c5d-9e0f-112233445566"
    with pytest.raises(ValidationError, match="UUID"):
        UsersDB(
            users=[
                User(name="a", connections=[vless(uuid=same)]),
                User(name="b", connections=[vless(uuid=same)]),
            ]
        )
    with pytest.raises(ValidationError, match="логин"):
        UsersDB(
            users=[
                User(name="a", connections=[naive("x")]),
                User(name="b", connections=[naive("x")]),
            ]
        )


# ─── настройки ───────────────────────────────────────────────────────────────


def test_settings_defaults_match_transferbox() -> None:
    s = Settings(domain="tst.example.com")
    assert s.site.template == "aether"
    assert (s.vless.ws_path, s.vless.grpc_service, s.vless.httpupgrade_path) == ("/", "", "/xhttp")
    assert (s.mieru.enabled, s.mieru.port) == (False, 21000)
    assert (s.backup.enabled, s.backup.hour, s.backup.minute) == (False, 12, 0)
    assert s.ssh.auth_mode == "password"


def test_settings_domain_normalized_and_validated() -> None:
    assert Settings(domain=" TST.Example.Com ").domain == "tst.example.com"
    for bad in ("localhost", "bad domain.com", "-a.com", ""):
        with pytest.raises(ValidationError):
            Settings(domain=bad)


def test_settings_paths_validated() -> None:
    s = Settings(domain="a.example.com")
    with pytest.raises(ValidationError):
        s.vless.ws_path = "no-slash"
    s.vless.grpc_service = "/svc"
    assert s.vless.grpc_service == "svc"


@pytest.mark.parametrize(("field", "value"), [("port", 0), ("port", 70000)])
def test_mieru_port_range(field: str, value: int) -> None:
    s = Settings(domain="a.example.com")
    with pytest.raises(ValidationError):
        setattr(s.mieru, field, value)


def test_unknown_site_template() -> None:
    s = Settings(domain="a.example.com")
    with pytest.raises(ValidationError):
        s.site.template = "unknown"
