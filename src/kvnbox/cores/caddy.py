"""Caddyfile: TLS, NaiveProxy (forward_proxy), маршрутизация VLESS, подписки, фейковый сайт."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from kvnbox.core.models import CoreKind, PasswordCredentials, Settings, Transport, User
from kvnbox.core.paths import Paths
from kvnbox.cores import ports
from kvnbox.cores.base import RenderedFile, active_connections, fill_template, load_asset
from kvnbox.cores.xray import cdn_active, xray_running

TEMPLATE = "Caddyfile.tmpl"


def forward_proxy_block(users: Sequence[User]) -> str:
    rules = [
        f"basic_auth {c.credentials.username} {c.credentials.password}"
        for _, c in active_connections(users, CoreKind.CADDY)
        if isinstance(c.credentials, PasswordCredentials)
    ]
    if not rules:
        return "# No NaiveProxy users configured"
    body = "\n        ".join(
        [
            *rules,
            "hide_ip",
            "hide_via",
            "probe_resistance",
            f"upstream socks5://{ports.LOCALHOST}:{ports.NAIVE_UPSTREAM}",
        ]
    )
    return f"forward_proxy {{\n        {body}\n    }}"


_FAKESITE_ON_ERROR = """            @error status 400 401 403 404 502 503
            handle_response @error {{
                root * {fakesite}
                rewrite * /index.html
                file_server
            }}"""


def xray_routes(settings: Settings, users: Sequence[User], paths: Paths) -> str:
    """Маршруты Xray (этап 11) — перед маршрутами sing-box. Пусто, если Xray не нужен:
    тогда Caddyfile байт в байт как без Xray."""
    if not xray_running(settings, users):
        return ""
    x = settings.xray
    err = _FAKESITE_ON_ERROR.format(
        fakesite=(paths.fakesite_dir / settings.site.template).as_posix()
    )
    p = ports.XRAY
    return f"""    # Xray: VLESS gRPC by service name (before the generic gRPC route of sing-box)
    @xray_grpc {{
        header Content-Type application/grpc*
        path /{x.grpc_service}/*
    }}
    handle @xray_grpc {{
        reverse_proxy {ports.LOCALHOST}:{p[Transport.GRPC]} {{
            transport http {{
                versions h2c
            }}
{err}
        }}
    }}

    # Xray: VLESS WS
    @xray_websocket {{
        header Connection *Upgrade*
        header Upgrade    websocket
        path {x.ws_path}
    }}
    handle @xray_websocket {{
        reverse_proxy {ports.LOCALHOST}:{p[Transport.WS]} {{
{err}
        }}
    }}

    # Xray: VLESS HTTPUpgrade
    handle {x.httpupgrade_path} {{
        reverse_proxy {ports.LOCALHOST}:{p[Transport.HTTPUPGRADE]} {{
{err}
        }}
    }}

    # Xray: VLESS XHTTP
    @xray_xhttp {{
        path {x.xhttp_path} {x.xhttp_path}/*
    }}
    handle @xray_xhttp {{
        reverse_proxy {ports.LOCALHOST}:{p[Transport.XHTTP]} {{
            transport http {{
                versions h2c 1.1
            }}
            flush_interval -1
{err}
        }}
    }}

{cdn_route(settings, users, err)}"""


def cdn_route(settings: Settings, users: Sequence[User], err: str) -> str:
    """Протокол CDN: принимается только с именем origin-поддомена (с ним приходит CDN),
    напрямую по основному домену этот путь не работает."""
    if not cdn_active(settings, users):
        return ""
    path = settings.xray.cdn_path
    return f"""    # Xray: VLESS XHTTP via CDN (only with the CDN origin host name)
    @xray_cdn {{
        host {settings.cdn.origin_host}
        path {path} {path}/*
    }}
    handle @xray_cdn {{
        reverse_proxy {ports.LOCALHOST}:{ports.XRAY[Transport.CDN]} {{
            transport http {{
                versions h2c 1.1
            }}
            flush_interval -1
{err}
        }}
    }}

"""


CDN_HEALTH_PATH = "/health"
CDN_HEALTH_BODY = '{"status":"ok"}'
# Время (мс, часы этого сервера), когда запрос дошёл до origin: тест CDN делит путь
# «сервер → CDN → origin → CDN → сервер» на «туда» и «обратно» по одним часам.
CDN_TIME_HEADER = "X-Origin-Time"


def cdn_health(settings: Settings) -> str:
    """Проверочный ответ для цепочки «CDN → origin»: только с именем origin-поддомена,
    работает, как только CDN настроен (ещё до первых подключений и без Xray)."""
    if not settings.cdn.origin_host:
        return ""
    return f"""    # CDN check: answers only with the CDN origin host name
    @cdn_health {{
        host {settings.cdn.origin_host}
        path {CDN_HEALTH_PATH}
    }}
    handle @cdn_health {{
        header Content-Type application/json
        header Cache-Control no-store
        header {CDN_TIME_HEADER} {{time.now.unix_ms}}
        respond `{CDN_HEALTH_BODY}` 200
    }}

"""


def extra_hosts(settings: Settings) -> str:
    """Origin-поддомен CDN в адресе сайта → Caddy получает на него свой сертификат.
    Пусто, пока CDN не настроен (Caddyfile байт в байт как раньше)."""
    return f", {settings.cdn.origin_host}" if settings.cdn.origin_host else ""


_SITE_LOG = "    log {\n        output stderr\n        level ERROR\n    }"


def cdn_log_block(settings: Settings, log_file: Path) -> str:
    """«Журнал CDN» (Диагностика): журнал запросов Caddy в файл — только запросы CDN
    (origin-поддомен + путь CDN), всё остальное пропускается (`log_skip`)."""
    host, path = settings.cdn.origin_host, settings.xray.cdn_path
    return (
        "    @kvn_not_cdn {\n"
        "        not {\n"
        f"            host {host}\n"
        f"            path {path} {path}/*\n"
        "        }\n"
        "    }\n"
        "    log_skip @kvn_not_cdn\n"
        "    log {\n"
        f"        output file {log_file.as_posix()}\n"
        "        format json\n"
        "    }"
    )


def render_caddyfile(
    settings: Settings, users: Sequence[User], paths: Paths, cdn_log: Path | None = None
) -> str:
    text = _render_caddyfile(settings, users, paths)
    if cdn_log is not None:
        text = text.replace(_SITE_LOG, cdn_log_block(settings, cdn_log), 1)
    return text


def _render_caddyfile(settings: Settings, users: Sequence[User], paths: Paths) -> str:
    email = settings.admin_email
    return fill_template(
        load_asset(TEMPLATE),
        {
            "DOMAIN": settings.domain,
            "CADDY_TLS_EMAIL": f"email {email}" if email else "# anonymous ACME",
            "FORWARD_PROXY_BLOCK": forward_proxy_block(users),
            "XRAY_ROUTES": cdn_health(settings) + xray_routes(settings, users, paths),
            "EXTRA_HOSTS": extra_hosts(settings),
            "FAKESITE_DIR": (paths.fakesite_dir / settings.site.template).as_posix(),
            "SUB_DIR": paths.sub_dir.as_posix(),
            "VLESS_WS_PATH": settings.vless.ws_path,
            "VLESS_HTTPUPGRADE_PATH": settings.vless.httpupgrade_path,
            "PORT_VLESS_WS": str(ports.VLESS[Transport.WS]),
            "PORT_VLESS_GRPC": str(ports.VLESS[Transport.GRPC]),
            "PORT_VLESS_HTTPUPGRADE": str(ports.VLESS[Transport.HTTPUPGRADE]),
        },
    )


def render(settings: Settings, users: Sequence[User], paths: Paths) -> list[RenderedFile]:
    return [RenderedFile(paths.caddy_config, render_caddyfile(settings, users, paths), 0o600)]
