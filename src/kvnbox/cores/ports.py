"""Локальные порты бэкендов за Caddy — единственное место, где они заданы.

Caddy принимает весь трафик на 443 и проксирует VLESS на локальные inbound'ы
sing-box; NaiveProxy (forward_proxy в Caddy) выходит в интернет через socks
sing-box. Значения совпадают с TransferBox.
"""

from __future__ import annotations

from kvnbox.core.models import Transport

LOCALHOST = "127.0.0.1"

NAIVE_UPSTREAM = 10001

VLESS = {
    Transport.WS: 10002,
    Transport.GRPC: 10003,
    Transport.HTTPUPGRADE: 10004,
}

# Ядро Xray (этап 11): свои локальные порты; Caddy разводит трафик по пути
XRAY = {
    Transport.WS: 10012,
    Transport.GRPC: 10013,
    Transport.HTTPUPGRADE: 10014,
    Transport.XHTTP: 10015,
}
