"""Чтение данных TransferBox (users.json + instance.env) в модели kvnBox.

Только для тестов (golden-сравнение со старой системой). Те же правила (D-020)
реализует разовый скрипт миграции вне репозитория.
"""

from __future__ import annotations

import json
from pathlib import Path

from kvnbox.core.models import (
    Connection,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UsersDB,
    UuidCredentials,
    default_core,
)

TRANSPORTS = {"ws": Transport.WS, "grpc": Transport.GRPC, "xhttp": Transport.HTTPUPGRADE}


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()
    return env


def _bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    return env.get(key, str(default)).lower() == "true"


def settings_from_env(env: dict[str, str]) -> Settings:
    return Settings.model_validate(
        {
            "domain": env["DOMAIN"],
            "admin_email": env.get("ADMIN_EMAIL", ""),
            "fqdn": env.get("FQDN", ""),
            "bbr_enabled": _bool(env, "BBR_ENABLED", True),
            "site": {"template": env.get("FAKE_SITE_TEMPLATE", "aether")},
            "vless": {
                "ws_path": env.get("VLESS_WS_PATH", "/"),
                "grpc_service": env.get("VLESS_GRPC_SERVICE", ""),
                "httpupgrade_path": env.get("VLESS_XHTTP_PATH", "/xhttp"),
            },
            "mieru": {
                "enabled": _bool(env, "MIERU_ENABLED"),
                "port": int(env.get("MIERU_PORT", "21000")),
            },
            "backup": {
                "enabled": _bool(env, "BACKUP_ENABLED"),
                "hour": int(env.get("BACKUP_HOUR", "12")),
                "minute": int(env.get("BACKUP_MINUTE", "0")),
                "password": env.get("BACKUP_PASSWORD", ""),
            },
            "telegram": {
                "enabled": _bool(env, "TG_BOT_ENABLED"),
                "token": env.get("TG_BOT_TOKEN", ""),
                "chat_id": env.get("TG_CHAT_ID", ""),
                "alerts_enabled": _bool(env, "TG_ALERTS_ENABLED"),
            },
            "security": {"scanner_protection": _bool(env, "SECURITY_SCANNER_PROTECTION")},
            "ssh": {"auth_mode": env.get("SSH_AUTH_MODE", "password")},
            "netbird": {
                "setup_key": env.get("NETBIRD_SETUP_KEY", ""),
                "management_url": env.get("NETBIRD_MANAGEMENT_URL", ""),
            },
        }
    )


def users_from_records(records: list[dict]) -> UsersDB:  # type: ignore[type-arg]
    grouped: dict[str, dict] = {}  # type: ignore[type-arg]  # порядок первого появления
    for rec in records:
        protocol = Protocol(rec["protocol"])
        creds = rec.get("credentials", {})
        transport = TRANSPORTS[creds.get("type", "ws")] if protocol is Protocol.VLESS else None
        credentials: UuidCredentials | PasswordCredentials = (
            UuidCredentials(uuid=creds["uuid"])
            if protocol is Protocol.VLESS
            else PasswordCredentials(username=creds["username"], password=creds["password"])
        )
        enabled = rec.get("enabled", True)
        entry = grouped.setdefault(rec["nickname"], {"tokens": [], "connections": []})
        entry["tokens"].append((enabled, rec.get("sub_token")))
        entry["connections"].append(
            Connection(
                protocol=protocol,
                transport=transport,
                core=default_core(protocol, transport),
                enabled=enabled,
                credentials=credentials,
            )
        )
    users = []
    for name, entry in grouped.items():
        tokens = [t for _, t in entry["tokens"] if t]
        enabled_tokens = [t for en, t in entry["tokens"] if en and t]
        token = (enabled_tokens or tokens)[0]
        users.append(User(name=name, sub_token=token, connections=entry["connections"]))
    return UsersDB(users=users)


def load_transferbox(directory: Path) -> tuple[Settings, UsersDB]:
    settings = settings_from_env(read_env(directory / "instance.env"))
    records = json.loads((directory / "users.json").read_text(encoding="utf-8"))
    return settings, users_from_records(records)
