"""Сертификат домена, который выпустил Caddy: где лежит и до какого числа действует."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from kvnbox.system.runner import Runner


def find_cert(caddy_data: Path, domain: str) -> Path | None:
    """`<caddy_data>/certificates/<выпускающий>/<домен>/<домен>.crt`, самый свежий."""
    found = list((caddy_data / "certificates").glob(f"*/{domain}/{domain}.crt"))
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def parse_enddate(text: str) -> dt.datetime | None:
    """`notAfter=Feb  2 16:14:16 2027 GMT` → datetime (UTC)."""
    value = text.strip().removeprefix("notAfter=").strip()
    try:
        return dt.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def cert_expiry(runner: Runner, path: Path) -> dt.datetime | None:
    res = runner.run(["openssl", "x509", "-noout", "-enddate", "-in", str(path)], timeout=15)
    return parse_enddate(res.stdout) if res.ok else None


def last_acme_error(journal: str) -> str:
    """Последняя ошибка получения/продления сертификата из журнала Caddy (JSON-строки)."""
    for line in reversed(journal.splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("level") != "error":
            continue
        text = f"{entry.get('msg', '')} {entry.get('error', '')}"
        if any(k in text.lower() for k in ("obtain", "renew", "acme", "certificate")):
            return " ".join(text.split())[:300]
    return ""
