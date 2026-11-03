"""Caddyfile: TLS, NaiveProxy (forward_proxy), маршрутизация VLESS, подписки, фейковый сайт."""

from __future__ import annotations

from collections.abc import Sequence

from kvnbox.core.models import CoreKind, PasswordCredentials, Settings, Transport, User
from kvnbox.core.paths import Paths
from kvnbox.cores import ports
from kvnbox.cores.base import RenderedFile, active_connections, fill_template, load_asset

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


def render_caddyfile(settings: Settings, users: Sequence[User], paths: Paths) -> str:
    email = settings.admin_email
    return fill_template(
        load_asset(TEMPLATE),
        {
            "DOMAIN": settings.domain,
            "CADDY_TLS_EMAIL": f"email {email}" if email else "# anonymous ACME",
            "FORWARD_PROXY_BLOCK": forward_proxy_block(users),
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
