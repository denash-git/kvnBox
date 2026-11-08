"""Действия API. Регистр `ACTIONS`: имя → (функция, меняет ли данные).

Функция получает `Ctx` (приложение, параметры, `emit` для промежуточного вывода долгих операций)
и возвращает JSON-совместимое значение. Ошибка для пользователя — `ServiceError`/`ApiError`.
"""

from __future__ import annotations

import base64
import dataclasses
import enum
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kvnbox import __version__
from kvnbox.api import API_VERSION
from kvnbox.clients import awg as awg_client
from kvnbox.clients import links
from kvnbox.core.models import (
    FAKE_SITE_TEMPLATES,
    SITE_TEMPLATE_LABELS,
    AwgCredentials,
    Connection,
    CoreKind,
    Protocol,
    Transport,
    User,
)
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import ApplyResult
from kvnbox.services.modules import (
    AwgModule,
    BbrModule,
    CaddyModule,
    MieruModule,
    NetbirdModule,
    ServiceInfo,
    SingboxModule,
    UpdateOutcome,
    XrayModule,
)

Json = Any


class ApiError(ServiceError):
    """Ошибка в параметрах запроса."""


@dataclass
class Ctx:
    app: App
    args: dict[str, Any]
    emit: Callable[[str], None] = field(default=lambda line: None)
    cancelled: Callable[[], bool] = field(default=lambda: False)

    # ── параметры ────────────────────────────────────────────────────────────

    def text(self, name: str, default: str | None = None) -> str:
        v = self.args.get(name, default)
        if v is None:
            raise ApiError(f"Не задан параметр «{name}».")
        if not isinstance(v, str):
            raise ApiError(f"Параметр «{name}» должен быть строкой.")
        return v

    def opt_str(self, name: str) -> str | None:
        v = self.args.get(name)
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ApiError(f"Параметр «{name}» должен быть строкой.")
        return v

    def num(self, name: str, default: int | None = None) -> int:
        v = self.args.get(name, default)
        if isinstance(v, bool) or not isinstance(v, int):
            if isinstance(v, str) and v.isdigit():
                return int(v)
            raise ApiError(f"Параметр «{name}» должен быть числом.")
        return v

    def flag(self, name: str, default: bool | None = None) -> bool:
        v = self.args.get(name, default)
        if not isinstance(v, bool):
            raise ApiError(f"Параметр «{name}» должен быть true/false.")
        return v


Action = Callable[[Ctx], Json]
ACTIONS: dict[str, tuple[Action, bool]] = {}


def action(name: str, mutating: bool = False) -> Callable[[Action], Action]:
    def deco(fn: Action) -> Action:
        ACTIONS[name] = (fn, mutating)
        return fn

    return deco


# ─── преобразование в JSON ───────────────────────────────────────────────────


def jsonable(obj: Any) -> Json:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return jsonable(obj.model_dump(mode="json"))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def result(res: ApplyResult) -> Json:
    if not res.ok:
        raise ServiceError(res.message)
    return {"ok": True, "message": res.message, "log": list(res.log)}


def outcome(res: UpdateOutcome) -> Json:
    if not res.ok:
        raise ServiceError(res.message)
    return {"ok": True, "message": res.message, "rolled_back": res.rolled_back}


# ─── общее ───────────────────────────────────────────────────────────────────

SLOTS: dict[str, tuple[Protocol, Transport | None]] = {
    "vless-ws": (Protocol.VLESS, Transport.WS),
    "vless-grpc": (Protocol.VLESS, Transport.GRPC),
    "vless-httpupgrade": (Protocol.VLESS, Transport.HTTPUPGRADE),
    "vless-xhttp": (Protocol.VLESS, Transport.XHTTP),
    "vless-cdn": (Protocol.VLESS, Transport.CDN),
    "naive": (Protocol.NAIVE, None),
    "mieru": (Protocol.MIERU, None),
    "awg": (Protocol.AWG, None),
}
SLOT_LABELS = {
    "vless-ws": "VLESS WS",
    "vless-grpc": "VLESS gRPC",
    "vless-httpupgrade": "VLESS HTTPUpgrade",
    "vless-xhttp": "VLESS XHTTP",
    "vless-cdn": "VLESS CDN",
    "naive": "NaiveProxy",
    "mieru": "Mieru",
    "awg": "AmneziaWG",
}
NAME_RE = re.compile(r"[a-z0-9_-]{1,32}")


def slot_of(conn: Connection) -> str:
    return next(k for k, v in SLOTS.items() if v == conn.slot)


def parse_slot(name: str) -> tuple[Protocol, Transport | None]:
    if name not in SLOTS:
        raise ApiError(f"Неизвестный протокол: {name}")
    return SLOTS[name]


def user_name(ctx: Ctx, key: str = "name") -> str:
    name = ctx.text(key).strip().lower()
    if not NAME_RE.fullmatch(name):
        raise ApiError("Имя: латиница, цифры, «-» и «_» (до 32 символов).")
    return name


def svc_users(app: App) -> Any:
    from kvnbox.services.users import UserService

    return UserService(app)


# ─── мета ────────────────────────────────────────────────────────────────────


@action("api.version")
def api_version(ctx: Ctx) -> Json:
    return {
        "api": API_VERSION,
        "panel": __version__,
        "client_ip": client_ip(),
        "actions": sorted(ACTIONS),
    }


def client_ip() -> str:
    """IP компьютера, с которого открыт канал (по SSH-сессии)."""
    import os

    parts = (os.environ.get("SSH_CLIENT") or os.environ.get("SSH_CONNECTION") or "").split()
    return parts[0] if parts else ""


# ─── обзор и службы ──────────────────────────────────────────────────────────

UNITS: dict[str, str] = {  # служба → подпись
    "caddy": "Caddy",
    "sing-box": "sing-box",
    "xray": "Xray",
    "mita": "Mieru",
    "kvnbox-awg": "AmneziaWG",
    "netbird": "NetBird",
    "kvnbox-bot": "Telegram-бот",
    "fail2ban": "Fail2ban",
}


@action("status.overview")
def status_overview(ctx: Ctx) -> Json:
    from kvnbox.services.status import StatusService

    ov = StatusService(ctx.app).overview()
    s = ctx.app.settings.load()
    data: dict[str, Any] = jsonable(ov)
    data.update(
        {
            "update_available": ov.update_available,
            "site_template": s.site.template,
            "users": len(ctx.app.users.all()),
            "users_active": sum(1 for u in ctx.app.users.all() if u.active),
            "awg_enabled": s.awg.enabled,
            "xray_enabled": s.xray.enabled,
        }
    )
    return data


@action("status.resources")
def status_resources(ctx: Ctx) -> Json:
    from kvnbox.services.diagnostics import DiagnosticsService

    snap = DiagnosticsService(ctx.app).snapshot(interval=0.5)
    return jsonable(snap)


@action("status.services")
def status_services(ctx: Ctx) -> Json:
    out = []
    for unit, label in UNITS.items():
        installed = unit in ("kvnbox-bot",) or _unit_installed(ctx.app, unit)
        state = ctx.app.systemd.state(unit) if installed else "not_installed"
        out.append({"unit": unit, "label": label, "installed": installed, "state": state})
    return out


def _unit_installed(app: App, unit: str) -> bool:
    binary = {
        "caddy": "caddy",
        "sing-box": "sing-box",
        "xray": "xray",
        "mita": "mita",
        "netbird": "netbird",
        "fail2ban": "fail2ban-client",
    }.get(unit)
    if unit == "kvnbox-awg":
        return app.paths.awg_bin.exists()
    return bool(binary and app.installed(binary))


def _unit(ctx: Ctx) -> str:
    unit = ctx.text("unit")
    if unit not in UNITS:
        raise ApiError(f"Неизвестная служба: {unit}")
    return unit


@action("service.restart", mutating=True)
def service_restart(ctx: Ctx) -> Json:
    unit = _unit(ctx)
    res = ctx.app.systemd.restart_clean(unit)
    if not res.ok:
        raise ServiceError(f"{UNITS[unit]}: не удалось перезапустить. {res.output.strip()}")
    return {"message": f"{UNITS[unit]} перезапущен."}


@action("service.start", mutating=True)
def service_start(ctx: Ctx) -> Json:
    unit = _unit(ctx)
    res = ctx.app.systemd.start(unit)
    if not res.ok:
        raise ServiceError(f"{UNITS[unit]}: не удалось запустить. {res.output.strip()}")
    return {"message": f"{UNITS[unit]} запущен."}


@action("service.stop", mutating=True)
def service_stop(ctx: Ctx) -> Json:
    unit = _unit(ctx)
    res = ctx.app.systemd.stop(unit)
    if not res.ok:
        raise ServiceError(f"{UNITS[unit]}: не удалось остановить. {res.output.strip()}")
    return {"message": f"{UNITS[unit]} остановлен."}


@action("service.restart_all", mutating=True)
def service_restart_all(ctx: Ctx) -> Json:
    failed = []
    for unit in ("caddy", "sing-box", "xray", "mita", "kvnbox-awg", "netbird"):
        active = _unit_installed(ctx.app, unit) and ctx.app.systemd.is_active(unit)
        if active and not ctx.app.systemd.restart_clean(unit).ok:
            failed.append(UNITS[unit])
    if failed:
        raise ServiceError("Не перезапустились: " + ", ".join(failed))
    return {"message": "Службы перезапущены."}


@action("server.reboot", mutating=True)
def server_reboot(ctx: Ctx) -> Json:
    from kvnbox.services.diagnostics import DiagnosticsService

    DiagnosticsService(ctx.app).reboot_server()
    return {"message": "Сервер перезагружается, VPN будет недоступен 1–2 минуты."}


APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


@action("server.os_updates")
def server_os_updates(ctx: Ctx) -> Json:
    """Обновления пакетов Ubuntu: сколько ждёт установки и нужна ли перезагрузка.
    `refresh` — сначала обновить списки пакетов (apt-get update, до минуты)."""
    if ctx.flag("refresh", False):
        ctx.app.runner.run(["apt-get", "update", "-q"], timeout=180, env=APT_ENV)
    res = ctx.app.runner.run(["apt", "list", "--upgradable"], timeout=60, env=APT_ENV)
    rows = [ln for ln in res.stdout.splitlines() if "/" in ln and "[upgradable" in ln]
    return {
        "count": len(rows),
        "security": sum(1 for ln in rows if "-security" in ln),
        "packages": [ln.split("/")[0] for ln in rows][:50],
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }


@action("server.os_upgrade", mutating=True)
def server_os_upgrade(ctx: Ctx) -> Json:
    code, tail = _stream(
        ctx,
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "-y",
            "-o",
            "Dpkg::Options::=--force-confold",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "upgrade",
        ],
        3600,
    )
    if code != 0:
        raise ServiceError(f"Обновление пакетов не удалось.\n{tail}")
    reboot = Path("/var/run/reboot-required").exists()
    return {
        "message": "Пакеты обновлены." + (" Нужна перезагрузка сервера." if reboot else ""),
        "reboot_required": reboot,
    }


@action("config.recreate", mutating=True)
def config_recreate(ctx: Ctx) -> Json:
    from kvnbox.services.settings import SettingsService

    return result(SettingsService(ctx.app).recreate())


# ─── обновление панели ───────────────────────────────────────────────────────


@action("panel.check_update")
def panel_check_update(ctx: Ctx) -> Json:
    from kvnbox.services.update import UpdateService

    chk = UpdateService(ctx.app).check()
    return {"current": chk.current, "latest": chk.latest, "newer": chk.newer_available}


@action("panel.update", mutating=True)
def panel_update(ctx: Ctx) -> Json:
    from kvnbox.services.update import UpdateService

    svc = UpdateService(ctx.app)
    version = ctx.opt_str("version") or svc.check().latest
    ctx.emit(f"Обновление панели до {version}…")
    message = svc.update(version)
    # канал api serve работает на старом коде — лаунчер переподключается
    return {"message": message, "version": version, "reconnect": True}


# ─── пользователи ────────────────────────────────────────────────────────────


def _conn_info(app: App, user: User, conn: Connection, detail: bool) -> Json:
    slot = slot_of(conn)
    data: dict[str, Any] = {
        "id": conn.id,
        "slot": slot,
        "label": SLOT_LABELS[slot],
        "core": conn.core.value,
        "enabled": conn.enabled,
    }
    if detail:
        settings = app.settings.load()
        data["link"] = links.build_link(user, conn, settings)
        if conn.protocol is Protocol.AWG and isinstance(conn.credentials, AwgCredentials):
            data["conf"] = awg_client.client_conf(user, conn, settings)
            data["conf_name"] = awg_client.file_name(user)
            data["address"] = conn.credentials.address
        if conn.protocol is Protocol.MIERU:
            data["mieru"] = {
                "json": links.mieru_client_json(user, conn, settings),
                "clash": links.mieru_clash_yaml(user, conn, settings),
                "singbox": links.mieru_singbox_json(user, conn, settings),
            }
    return data


def _user_info(app: App, user: User, detail: bool = False) -> Json:
    domain = app.settings.load().domain
    return {
        "name": user.name,
        "active": user.active,
        "enabled": any(c.enabled for c in user.connections),
        "subscription": f"https://{domain}/sub/{user.sub_token}",
        "connections": [_conn_info(app, user, c, detail) for c in user.connections],
    }


@action("users.list")
def users_list(ctx: Ctx) -> Json:
    return [_user_info(ctx.app, u) for u in ctx.app.users.all()]


@action("users.get")
def users_get(ctx: Ctx) -> Json:
    user = svc_users(ctx.app).get(user_name(ctx))
    return _user_info(ctx.app, user, detail=True)


@action("users.slots")
def users_slots(ctx: Ctx) -> Json:
    """Какие протоколы сейчас можно добавить и на каких ядрах."""
    from kvnbox.core.models import supported_cores

    svc = svc_users(ctx.app)
    s = ctx.app.settings.load()
    out = []
    for slot, (proto, transport) in SLOTS.items():
        reason = ""
        if transport in (Transport.XHTTP,) and not svc.xray_ready():
            reason = "нужен Xray (Модули → Xray)"
        elif transport is Transport.CDN and not svc.cdn_ready():
            reason = "нужны Xray и настройки CDN"
        elif proto is Protocol.AWG and not (s.awg.enabled and s.awg.configured):
            reason = "включите AmneziaWG в модулях"
        elif proto is Protocol.MIERU and not s.mieru.enabled:
            reason = "включите Mieru в модулях"
        cores = [c.value for c in supported_cores(proto, transport)]
        if CoreKind.XRAY.value in cores and len(cores) > 1 and not svc.xray_ready():
            cores = [c for c in cores if c != CoreKind.XRAY.value]
        out.append(
            {
                "slot": slot,
                "label": SLOT_LABELS[slot],
                "available": not reason,
                "reason": reason,
                "cores": cores,
            }
        )
    return out


@action("users.add", mutating=True)
def users_add(ctx: Ctx) -> Json:
    name = user_name(ctx)
    proto, transport = parse_slot(ctx.text("slot"))
    core_name = ctx.opt_str("core")
    core = CoreKind(core_name) if core_name in ("singbox", "xray") else None
    _, res = svc_users(ctx.app).add_connection(name, proto, transport, core)
    data = result(res)
    data["user"] = _user_info(ctx.app, svc_users(ctx.app).get(name), detail=True)
    return data


@action("users.remove", mutating=True)
def users_remove(ctx: Ctx) -> Json:
    name = user_name(ctx)
    slot = ctx.opt_str("slot")
    svc = svc_users(ctx.app)
    if slot:
        return result(svc.remove_connection(name, *parse_slot(slot)))
    return result(svc.delete(name))


@action("users.set_enabled", mutating=True)
def users_set_enabled(ctx: Ctx) -> Json:
    name = user_name(ctx)
    enabled = ctx.flag("enabled")
    slot = ctx.opt_str("slot")
    svc = svc_users(ctx.app)
    if slot:
        proto, transport = parse_slot(slot)
        return result(svc.set_enabled(name, enabled, proto, transport))
    return result(svc.set_enabled(name, enabled))


@action("users.qr")
def users_qr(ctx: Ctx) -> Json:
    text = ctx.text("text")
    if len(text) > 2900:
        raise ApiError("Слишком длинный текст для QR-кода.")
    res = ctx.app.runner.run(["qrencode", "-t", "SVG", "-m", "2", "-o", "-", text], timeout=20)
    if not res.ok:
        raise ServiceError("Не удалось построить QR-код (нужен пакет qrencode).")
    return {"svg": res.stdout}


@action("users.send_telegram", mutating=True)
def users_send_telegram(ctx: Ctx) -> Json:
    slot = ctx.opt_str("slot")
    if slot:
        svc_users(ctx.app).send_to_telegram(user_name(ctx), parse_slot(slot))
        return {"message": f"{SLOT_LABELS[slot]}: отправлено в Telegram."}
    svc_users(ctx.app).send_to_telegram(user_name(ctx))
    return {"message": "Ссылки и QR-коды отправлены в Telegram."}


# ─── модули ──────────────────────────────────────────────────────────────────

MODULES = ("singbox", "xray", "caddy", "mieru", "awg", "netbird")


def _info(i: ServiceInfo) -> Json:
    return {"unit": i.unit, "installed": i.installed, "state": i.state, "version": i.version}


def module_users(app: App, mid: str) -> list[str]:
    """Пользователи с включёнными подключениями на модуле (Xray, Mieru, AmneziaWG)."""

    def uses(c: Connection) -> bool:
        if mid == "xray":
            return c.core is CoreKind.XRAY
        if mid == "mieru":
            return c.protocol is Protocol.MIERU
        if mid == "awg":
            return c.protocol is Protocol.AWG
        return False

    return [u.name for u in app.users.all() if any(c.enabled and uses(c) for c in u.connections)]


@action("modules.list")
def modules_list(ctx: Ctx) -> Json:
    app = ctx.app
    s = app.settings.load()
    xray = XrayModule(app)
    awg = AwgModule(app)
    nb = NetbirdModule(app)
    from kvnbox.services.awg import AwgService

    peers = AwgService(app).peer_states() if s.awg.enabled and awg.built() else []
    return [
        {
            "id": "singbox",
            "name": "sing-box",
            **_info(SingboxModule(app).info()),
            "enabled": True,
            "note": "VLESS WS, gRPC, HTTPUpgrade",
        },
        {
            "id": "xray",
            "name": "Xray",
            **_info(xray.info()),
            "enabled": s.xray.enabled,
            "users_on": len(module_users(app, "xray")),
            "note": "VLESS XHTTP и CDN",
        },
        {
            "id": "caddy",
            "name": "Caddy + NaiveProxy",
            **_info(CaddyModule(app).info()),
            "enabled": True,
            "note": "Сайт-маскировка, сертификаты, NaiveProxy",
        },
        {
            "id": "mieru",
            "name": "Mieru",
            **_info(MieruModule(app).info()),
            "enabled": s.mieru.enabled,
            "users_on": len(module_users(app, "mieru")),
            "port": s.mieru.port,
            "note": f"Порт {s.mieru.port}/tcp",
        },
        {
            "id": "awg",
            "name": "AmneziaWG 3.1",
            **_info(awg.info()),
            "enabled": s.awg.enabled,
            "users_on": len(module_users(app, "awg")),
            "port": s.awg.port,
            "target_version": awg.target_version(),
            "update_available": awg.update_available(),
            "peers": [jsonable(p) for p in peers],
            "note": f"Порт {s.awg.port}/udp" if s.awg.enabled else "Выключен",
        },
        {
            "id": "netbird",
            "name": "NetBird",
            "unit": "netbird",
            **jsonable(nb.status()),
            "enabled": nb.status().installed,
            "version": "",
            "has_setup_key": bool(s.netbird.setup_key),
            "management_url": s.netbird.management_url,
            "note": "Подключение сервера к своей сети NetBird",
        },
    ]


@action("modules.latest")
def modules_latest(ctx: Ctx) -> Json:
    mid = ctx.text("id")
    app = ctx.app
    if mid == "singbox":
        return {"latest": SingboxModule(app).latest_version()}
    if mid == "xray":
        return {"latest": XrayModule(app).latest_version()}
    if mid == "caddy":
        return {"latest": CaddyModule(app).latest_version()}
    if mid == "mieru":
        return {"latest": MieruModule(app).latest_version()}
    if mid == "awg":
        return {"latest": AwgModule(app).target_version()}
    raise ApiError("У этого модуля нет проверки версии.")


def _stream(ctx: Ctx, cmd: list[str], timeout: int) -> tuple[int, str]:
    """Долгая команда с выводом строк в `emit` (сборка Caddy и AmneziaWG)."""
    lines: list[str] = []
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    ) as proc:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).rstrip()
            if line:
                lines.append(line)
                ctx.emit(line)
        code = proc.wait(timeout=timeout)
    return code, "\n".join(lines[-5:])


@action("modules.update", mutating=True)
def modules_update(ctx: Ctx) -> Json:
    mid = ctx.text("id")
    tag = ctx.opt_str("version")
    app = ctx.app
    if mid == "singbox":
        return outcome(SingboxModule(app).safe_update(tag))
    if mid == "xray":
        return outcome(XrayModule(app).safe_update(tag))
    if mid == "mieru":
        return outcome(MieruModule(app).safe_update(tag))
    if mid == "caddy":
        mod = CaddyModule(app)
        tag = tag or mod.latest_version()
        code, tail = _stream(ctx, mod.rebuild_command(tag), 1800)
        if code != 0:
            raise ServiceError(f"Обновление Caddy не удалось.\n{tail}")
        return {"message": f"Caddy обновлён до {tag}."}
    if mid == "awg":
        return _awg_build(ctx, force=True)
    if mid == "netbird":
        res = NetbirdModule(app).update()
        if not res.ok:
            raise ServiceError(f"Обновление NetBird не удалось: {res.output.strip()}")
        return {"message": "NetBird обновлён."}
    raise ApiError(f"Неизвестный модуль: {mid}")


def _awg_build(ctx: Ctx, force: bool) -> Json:
    mod = AwgModule(ctx.app)
    if force:
        marker = Path(f"{ctx.app.paths.awg_bin}.version")
        marker.unlink(missing_ok=True)
    code, tail = _stream(ctx, mod.build_command(), 1800)
    if code != 0 or not mod.built():
        raise ServiceError(f"Сборка AmneziaWG не удалась.\n{tail}")
    return {"message": f"AmneziaWG собран ({mod.version()})."}


@action("modules.set_enabled", mutating=True)
def modules_set_enabled(ctx: Ctx) -> Json:
    mid = ctx.text("id")
    enabled = ctx.flag("enabled")
    app = ctx.app
    if not enabled and (busy := module_users(app, mid)):
        raise ServiceError(
            f"Нельзя выключить: включённые подключения у {len(busy)} польз. "
            f"({', '.join(busy[:5])}{'…' if len(busy) > 5 else ''}). "
            "Сначала выключите или удалите их."
        )
    if mid == "xray":
        return result(XrayModule(app).set_enabled(enabled))
    if mid == "mieru":
        return result(MieruModule(app).set_enabled(enabled))
    if mid == "awg":
        mod = AwgModule(app)
        if enabled and not mod.built():
            ctx.emit("AmneziaWG ещё не собран — сборка (1–2 минуты)…")
            _awg_build(ctx, force=False)
        data = result(mod.set_enabled(enabled))
        data["port"] = app.settings.load().awg.port
        return data
    if mid == "singbox":
        unit = "sing-box"
    elif mid == "caddy":
        unit = "caddy"
    elif mid == "netbird":
        unit = "netbird"
    else:
        raise ApiError(f"Неизвестный модуль: {mid}")
    res = app.systemd.start(unit) if enabled else app.systemd.stop(unit)
    if not res.ok:
        raise ServiceError(res.output.strip() or "Не удалось изменить состояние службы.")
    return {"message": f"{unit}: {'запущен' if enabled else 'остановлен'}."}


@action("modules.install", mutating=True)
def modules_install(ctx: Ctx) -> Json:
    mid = ctx.text("id")
    app = ctx.app
    if mid == "xray":
        return result(XrayModule(app).install())
    if mid == "mieru":
        return result(MieruModule(app).install())
    if mid == "netbird":
        NetbirdModule(app).install()
        return {"message": "NetBird установлен. Задайте Setup Key и подключитесь."}
    if mid == "awg":
        return _awg_build(ctx, force=False)
    raise ApiError("Этот модуль не устанавливается отдельно.")


@action("modules.uninstall", mutating=True)
def modules_uninstall(ctx: Ctx) -> Json:
    mid = ctx.text("id")
    app = ctx.app
    if mid == "xray":
        return result(XrayModule(app).uninstall())
    if mid == "mieru":
        return result(MieruModule(app).uninstall())
    if mid == "netbird":
        NetbirdModule(app).uninstall()
        return {"message": "NetBird удалён."}
    raise ApiError("Этот модуль не удаляется.")


@action("mieru.set_port", mutating=True)
def mieru_set_port(ctx: Ctx) -> Json:
    return result(MieruModule(ctx.app).set_port(ctx.num("port")))


@action("awg.set_port", mutating=True)
def awg_set_port(ctx: Ctx) -> Json:
    return result(AwgModule(ctx.app).set_port(ctx.num("port")))


@action("awg.regenerate", mutating=True)
def awg_regenerate(ctx: Ctx) -> Json:
    return result(AwgModule(ctx.app).regenerate())


@action("netbird.status")
def netbird_status(ctx: Ctx) -> Json:
    nb = NetbirdModule(ctx.app)
    st = nb.status()
    return {**jsonable(st), "detail": nb.detailed_status() if st.installed else ""}


@action("netbird.set_setup_key", mutating=True)
def netbird_set_setup_key(ctx: Ctx) -> Json:
    NetbirdModule(ctx.app).set_setup_key(ctx.text("key").strip())
    return {"message": "Setup Key сохранён."}


@action("netbird.set_management_url", mutating=True)
def netbird_set_management_url(ctx: Ctx) -> Json:
    NetbirdModule(ctx.app).set_management_url(ctx.text("url").strip())
    return {"message": "Management URL сохранён."}


@action("netbird.up", mutating=True)
def netbird_up(ctx: Ctx) -> Json:
    res = NetbirdModule(ctx.app).up()
    if not res.ok:
        raise ServiceError(f"Подключение NetBird не удалось: {res.output.strip()}")
    return {"message": "NetBird подключён."}


@action("bbr.status")
def bbr_status(ctx: Ctx) -> Json:
    return {"active": BbrModule(ctx.app).is_active()}


@action("bbr.set", mutating=True)
def bbr_set(ctx: Ctx) -> Json:
    mod = BbrModule(ctx.app)
    want = ctx.flag("enabled")
    if mod.is_active() == want:
        return {"message": "BBR уже " + ("включён." if want else "выключен."), "active": want}
    message = mod.toggle()
    return {"message": message, "active": mod.is_active()}


# ─── настройки ───────────────────────────────────────────────────────────────


def _mask(v: str) -> str:
    return "" if not v else v[:4] + "…" + v[-2:] if len(v) > 8 else "••••"


@action("settings.get")
def settings_get(ctx: Ctx) -> Json:
    s = ctx.app.settings.load()
    labels = {v: k for k, v in SITE_TEMPLATE_LABELS}
    return {
        "domain": s.domain,
        "admin_email": s.admin_email,
        "fqdn": s.fqdn,
        "site": {
            "template": s.site.template,
            "templates": [{"id": t, "label": labels.get(t, t)} for t in FAKE_SITE_TEMPLATES],
        },
        "vless": jsonable(s.vless),
        "xray": jsonable(s.xray),
        "cdn": {
            "domain": s.cdn.domain,
            "origin_host": s.cdn.origin_host,
            "path": s.xray.cdn_path,
            "configured": s.cdn.configured,
        },
        "mieru": {"enabled": s.mieru.enabled, "port": s.mieru.port},
        "awg": {"enabled": s.awg.enabled, "port": s.awg.port},
        "backup": {
            "enabled": s.backup.enabled,
            "hour": s.backup.hour,
            "minute": s.backup.minute,
            "password_set": bool(s.backup.password),
        },
        "telegram": {
            "enabled": s.telegram.enabled,
            "token": _mask(s.telegram.token),
            "chat_id": s.telegram.chat_id,
            "alerts_enabled": s.telegram.alerts_enabled,
        },
        "security": {"scanner_protection": s.security.scanner_protection},
        "ssh": {"auth_mode": s.ssh.auth_mode},
        "netbird": {
            "setup_key": _mask(s.netbird.setup_key),
            "management_url": s.netbird.management_url,
        },
        "bbr_enabled": s.bbr_enabled,
    }


def _settings(app: App) -> Any:
    from kvnbox.services.settings import SettingsService

    return SettingsService(app)


@action("settings.set_domain", mutating=True)
def settings_set_domain(ctx: Ctx) -> Json:
    domain = ctx.text("domain").strip().lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise ApiError("Некорректный домен.")
    email = ctx.opt_str("email") or ctx.app.settings.load().admin_email
    return result(_settings(ctx.app).set_domain_email(domain, email.strip()))


@action("settings.set_fqdn", mutating=True)
def settings_set_fqdn(ctx: Ctx) -> Json:
    _settings(ctx.app).set_fqdn(ctx.text("fqdn"))
    return {"message": "Имя VPS изменено."}


@action("settings.set_site_template", mutating=True)
def settings_set_site_template(ctx: Ctx) -> Json:
    return result(_settings(ctx.app).set_site_template(ctx.text("template")))


@action("settings.set_path", mutating=True)
def settings_set_path(ctx: Ctx) -> Json:
    return result(
        _settings(ctx.app).set_path(ctx.text("core"), ctx.text("field"), ctx.text("value"))
    )


@action("cdn.set", mutating=True)
def cdn_set(ctx: Ctx) -> Json:
    return result(_settings(ctx.app).set_cdn(ctx.text("domain", ""), ctx.text("origin_host", "")))


@action("cdn.test")
def cdn_test(ctx: Ctx) -> Json:
    from kvnbox.services.cdn_test import CdnTest, report_lines

    report = CdnTest(ctx.app).run()
    return {
        "ok": report.direct.ok and report.ok_count == len(report.probes),
        "lines": report_lines(report),
        "report": jsonable(report),
    }


# ─── Telegram-бот ────────────────────────────────────────────────────────────


def _bot(app: App) -> Any:
    from kvnbox.services.telegram_bot import BotService

    return BotService(app)


@action("telegram.state")
def telegram_state(ctx: Ctx) -> Json:
    return jsonable(_bot(ctx.app).state())


@action("telegram.configure", mutating=True)
def telegram_configure(ctx: Ctx) -> Json:
    token = ctx.text("token").strip()
    chat_id = ctx.text("chat_id").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise ApiError("Токен бота: вида 123456:ABC…")
    if not re.fullmatch(r"-?\d+", chat_id):
        raise ApiError("Chat ID — число.")
    _bot(ctx.app).configure(token, chat_id)
    return {"message": "Токен и Chat ID сохранены."}


@action("telegram.set_enabled", mutating=True)
def telegram_set_enabled(ctx: Ctx) -> Json:
    bot = _bot(ctx.app)
    if ctx.flag("enabled"):
        ok = bot.enable()
        return {"message": "Бот включён." + ("" if ok else " Тестовое сообщение не дошло.")}
    bot.disable()
    return {"message": "Бот выключен."}


@action("telegram.set_alerts", mutating=True)
def telegram_set_alerts(ctx: Ctx) -> Json:
    bot = _bot(ctx.app)
    want = ctx.flag("enabled")
    if bot.state().alerts != want:
        bot.toggle_alerts()
    return {"message": "Оповещения " + ("включены." if want else "выключены.")}


@action("telegram.test")
def telegram_test(ctx: Ctx) -> Json:
    if not _bot(ctx.app).send_test():
        raise ServiceError("Сообщение не отправлено: проверьте токен и Chat ID.")
    return {"message": "Тестовое сообщение отправлено."}


# ─── SSH и защита ────────────────────────────────────────────────────────────


def _ssh(app: App) -> Any:
    from kvnbox.services.ssh import SshService

    return SshService(app)


@action("ssh.status")
def ssh_status(ctx: Ctx) -> Json:
    st = _ssh(ctx.app).status()
    return {
        "port": st.port,
        "password_allowed": st.password_allowed,
        "keys": [
            {"index": k.index, "type": k.type, "fingerprint": k.fingerprint, "comment": k.comment}
            for k in st.keys
        ],
    }


@action("ssh.set_auth", mutating=True)
def ssh_set_auth(ctx: Ctx) -> Json:
    mode = ctx.text("mode")
    if mode not in ("password", "key"):
        raise ApiError("Режим: password или key.")
    return {"message": _ssh(ctx.app).set_auth_mode(mode)}


@action("ssh.set_port", mutating=True)
def ssh_set_port(ctx: Ctx) -> Json:
    old = _ssh(ctx.app).change_port(ctx.num("port"))
    return {
        "message": "Порт SSH изменён. Проверьте вход по новому порту и подтвердите.",
        "old_port": old,
    }


@action("ssh.confirm_port", mutating=True)
def ssh_confirm_port(ctx: Ctx) -> Json:
    _ssh(ctx.app).confirm_port_change(ctx.num("old_port"))
    return {"message": "Правило старого порта убрано."}


@action("ssh.add_key", mutating=True)
def ssh_add_key(ctx: Ctx) -> Json:
    _ssh(ctx.app).add_key(ctx.text("public_key").strip())
    return {"message": "Ключ добавлен."}


@action("ssh.delete_key", mutating=True)
def ssh_delete_key(ctx: Ctx) -> Json:
    return {"message": _ssh(ctx.app).delete_key(ctx.num("index"))}


@action("ssh.generate_key", mutating=True)
def ssh_generate_key(ctx: Ctx) -> Json:
    path = _ssh(ctx.app).generate_key()
    return {
        "message": "Ключ создан и добавлен на сервер.",
        "name": path.name,
        "private_key": path.read_text(encoding="utf-8"),
    }


def _security(app: App) -> Any:
    from kvnbox.services.security import SecurityService

    return SecurityService(app)


JAIL_LABELS = {
    "sshd": "Подбор пароля SSH",
    "portscan-trap": "Сканирование портов",
}


def _valid_ip(text: str) -> str:
    import ipaddress

    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError as e:
        raise ApiError("Это не IP-адрес.") from e


@action("security.status")
def security_status(ctx: Ctx) -> Json:
    """Сводка: сколько адресов заблокировано и по каким правилам (без самого списка)."""
    st = _security(ctx.app).status()
    me = client_ip()
    return {
        "installed": _security(ctx.app).is_installed(),
        "active": st.active,
        "total": len(st.banned_ips),
        "jails": [
            {"name": j.name, "label": JAIL_LABELS.get(j.name, j.name), "count": len(j.banned_ips)}
            for j in st.jails
        ],
        "my_ip": me,
        "my_ip_banned": bool(me) and me in st.banned_ips,
    }


@action("security.find")
def security_find(ctx: Ctx) -> Json:
    ip = _valid_ip(ctx.text("ip"))
    st = _security(ctx.app).status()
    jails = [JAIL_LABELS.get(j.name, j.name) for j in st.jails if ip in j.banned_ips]
    return {"ip": ip, "banned": bool(jails), "jails": jails}


@action("security.export")
def security_export(ctx: Ctx) -> Json:
    st = _security(ctx.app).status()
    rows = [
        f"{ip}\t{JAIL_LABELS.get(j.name, j.name)}" for j in st.jails for ip in sorted(j.banned_ips)
    ]
    return {"name": "kvnbox-banned-ips.txt", "text": "\n".join(rows) + ("\n" if rows else "")}


@action("security.set_enabled", mutating=True)
def security_set_enabled(ctx: Ctx) -> Json:
    sec = _security(ctx.app)
    return {"message": sec.enable() if ctx.flag("enabled") else sec.disable()}


@action("security.unban", mutating=True)
def security_unban(ctx: Ctx) -> Json:
    ip = ctx.opt_str("ip")
    sec = _security(ctx.app)
    if ip == "me":
        ip = client_ip()
        if not ip:
            raise ServiceError("Не удалось определить IP этого компьютера.")
    return {"message": sec.unban(_valid_ip(ip)) if ip else sec.unban_all()}


# ─── журналы ─────────────────────────────────────────────────────────────────


@action("logs.units")
def logs_units(ctx: Ctx) -> Json:
    return [
        {"unit": u, "label": label}
        for u, label in UNITS.items()
        if u == "kvnbox-bot" or _unit_installed(ctx.app, u)
    ]


@action("logs.tail")
def logs_tail(ctx: Ctx) -> Json:
    unit = _unit(ctx)
    lines = max(10, min(ctx.num("lines", 200), 2000))
    return {"unit": unit, "text": ctx.app.systemd.journal(unit, lines)}


@action("logs.follow")
def logs_follow(ctx: Ctx) -> Json:
    """Поток журнала (только в `api serve`): сначала последние строки, затем новые — в `emit`,
    пока не придёт отмена (проверка раз в 0,5 с, даже если служба молчит)."""
    import select

    unit = _unit(ctx)
    lines = max(0, min(ctx.num("lines", 100), 2000))
    with subprocess.Popen(
        ["journalctl", "-u", unit, "-f", "-n", str(lines), "-o", "short-iso", "--no-pager"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        try:
            while not ctx.cancelled():
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                line = proc.stdout.readline()
                if not line:
                    break
                ctx.emit(line.rstrip())
        finally:
            proc.terminate()
    return {"message": "Поток журнала остановлен."}


# ─── бэкапы ──────────────────────────────────────────────────────────────────


def _backup(app: App) -> Any:
    from kvnbox.services.backup import BackupService

    return BackupService(app)


def _backup_file(ctx: Ctx) -> Path:
    name = ctx.text("name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", name):
        raise ApiError("Некорректное имя файла бэкапа.")
    path = Path(_backup(ctx.app).directory) / name
    return path


@action("backup.list")
def backup_list(ctx: Ctx) -> Json:
    from kvnbox.services.backup import is_encrypted

    svc = _backup(ctx.app)
    s = ctx.app.settings.load()
    files = []
    for p in svc.all():
        st = p.stat()
        files.append(
            {
                "name": p.name,
                "size": st.st_size,
                "date": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes"),
                "encrypted": is_encrypted(p),
            }
        )
    return {
        "files": files,
        "password_set": bool(s.backup.password),
        "schedule": {"enabled": s.backup.enabled, "hour": s.backup.hour, "minute": s.backup.minute},
    }


@action("backup.create", mutating=True)
def backup_create(ctx: Ctx) -> Json:
    path = _backup(ctx.app).create()
    return {"message": f"Бэкап создан: {path.name}", "name": path.name}


@action("backup.restore", mutating=True)
def backup_restore(ctx: Ctx) -> Json:
    from kvnbox.services.backup import PasswordRequired

    path = _backup_file(ctx)
    if not path.exists():
        raise ApiError("Файл бэкапа не найден.")
    try:
        res = _backup(ctx.app).restore(path, ctx.opt_str("password"))
    except PasswordRequired as e:
        raise ServiceError(f"{e} Укажите пароль бэкапа.") from e
    if not res.ok:
        raise ServiceError(res.message)
    return {"message": res.message, "log": res.log}


@action("backup.download")
def backup_download(ctx: Ctx) -> Json:
    path = _backup_file(ctx)
    if not path.exists():
        raise ApiError("Файл бэкапа не найден.")
    return {"name": path.name, "base64": base64.b64encode(path.read_bytes()).decode()}


@action("backup.upload", mutating=True)
def backup_upload(ctx: Ctx) -> Json:
    from kvnbox.services.backup import PREFIX

    path = _backup_file(ctx)
    if not path.name.startswith(PREFIX):  # иначе файл не попадёт в список бэкапов
        path = path.with_name(f"{PREFIX}upload_{path.name}")
    data = base64.b64decode(ctx.text("base64"), validate=True)
    if len(data) > 50 * 1024 * 1024 or not data.startswith(b"PK"):
        raise ApiError("Это не архив бэкапа kvnBox.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    return {"message": f"Файл {path.name} загружен на сервер.", "name": path.name}


@action("backup.send_telegram")
def backup_send_telegram(ctx: Ctx) -> Json:
    from kvnbox.services.notify import NotifyError, Telegram

    path = _backup_file(ctx)
    if not path.exists():
        raise ApiError("Файл бэкапа не найден.")
    tg = Telegram(ctx.app.settings.load().telegram)
    if not tg.configured:
        raise ServiceError("Telegram-бот не настроен (Настройки → Telegram-бот).")
    try:
        tg.send_document(path, f"Бэкап kvnBox: {path.name}")
    except NotifyError as e:
        raise ServiceError(str(e)) from e
    return {"message": f"{path.name} отправлен в Telegram."}


@action("backup.delete", mutating=True)
def backup_delete(ctx: Ctx) -> Json:
    path = _backup_file(ctx)
    path.unlink(missing_ok=True)
    return {"message": f"{path.name} удалён."}


@action("backup.set_password", mutating=True)
def backup_set_password(ctx: Ctx) -> Json:
    _backup(ctx.app).set_password(ctx.text("password", ""))
    return {"message": "Пароль бэкапов " + ("задан." if ctx.text("password", "") else "убран.")}


@action("backup.set_schedule", mutating=True)
def backup_set_schedule(ctx: Ctx) -> Json:
    enabled = ctx.flag("enabled")
    hour = ctx.num("hour", 12)
    minute = ctx.num("minute", 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ApiError("Время: часы 0–23, минуты 0–59.")
    _backup(ctx.app).set_schedule(enabled, hour, minute)
    return {"message": "Расписание бэкапов " + ("включено." if enabled else "выключено.")}


# ─── диагностика ─────────────────────────────────────────────────────────────


def _diag(app: App) -> Any:
    from kvnbox.services.diagnostics import DiagnosticsService

    return DiagnosticsService(app)


@action("diag.public_ip")
def diag_public_ip(ctx: Ctx) -> Json:
    return {"ip": _diag(ctx.app).public_ip()}


@action("diag.speedtest_servers")
def diag_speedtest_servers(ctx: Ctx) -> Json:
    return jsonable(_diag(ctx.app).speedtest_servers())


@action("diag.speedtest")
def diag_speedtest(ctx: Ctx) -> Json:
    return speedtest_result(_diag(ctx.app).speedtest_json(ctx.opt_str("server_id")))


def speedtest_result(raw: str) -> Json:
    """JSON Ookla → итог для карточек; не JSON — как есть, в `text`."""
    import json

    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or "download" not in d:
            raise ValueError
    except ValueError:
        return {"text": raw.strip()}
    srv = d.get("server") or {}
    ping = d.get("ping") or {}

    def mbps(part: str) -> float:
        return round(float((d.get(part) or {}).get("bandwidth", 0)) * 8 / 1e6, 1)

    loss = d.get("packetLoss")
    return {
        "server": srv.get("name", ""),
        "location": ", ".join(x for x in (srv.get("location"), srv.get("country")) if x),
        "isp": d.get("isp", ""),
        "ping": round(float(ping.get("latency", 0)), 1),
        "jitter": round(float(ping.get("jitter", 0)), 1),
        "download": mbps("download"),
        "upload": mbps("upload"),
        "loss": None if loss is None else round(float(loss), 1),
        "url": (d.get("result") or {}).get("url", ""),
    }


@action("diag.iperf_start", mutating=True)
def diag_iperf_start(ctx: Ctx) -> Json:
    port = _diag(ctx.app).iperf_start()
    return {
        "port": port,
        "ip": _diag(ctx.app).public_ip(),
        "message": f"iperf3 ждёт клиента на порту {port} (10 минут).",
    }


@action("diag.iperf_stop", mutating=True)
def diag_iperf_stop(ctx: Ctx) -> Json:
    _diag(ctx.app).iperf_stop()
    return {"message": "iperf3 остановлен."}


# ─── выполнение ──────────────────────────────────────────────────────────────


def run_action(
    app: App,
    name: str,
    args: dict[str, Any],
    emit: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Json:
    if name not in ACTIONS:
        raise ApiError(f"Нет такого действия: {name}")
    fn, _ = ACTIONS[name]
    ctx = Ctx(app, args)
    if emit is not None:
        ctx.emit = emit
    if cancelled is not None:
        ctx.cancelled = cancelled
    return jsonable(fn(ctx))


def is_mutating(name: str) -> bool:
    return name in ACTIONS and ACTIONS[name][1]
