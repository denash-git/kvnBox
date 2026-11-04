"""Идентификаторы подключений в формате TransferBox: протокол + тип («ws», «grpc»,
«xhttp» или пусто). Используются в подписях кнопок и callback_data, чтобы бот
выглядел и работал как прежде."""

from __future__ import annotations

from kvnbox.core.models import Connection, Protocol, Transport

TYPE_BY_TRANSPORT = {Transport.WS: "ws", Transport.GRPC: "grpc", Transport.HTTPUPGRADE: "xhttp"}
TRANSPORT_BY_TYPE = {v: k for k, v in TYPE_BY_TRANSPORT.items()}


def proto_type(conn: Connection) -> tuple[str, str]:
    """(«vless», «xhttp») / («naive», «»)."""
    return conn.protocol.value, TYPE_BY_TRANSPORT[conn.transport] if conn.transport else ""


def proto_label(proto: str, utype: str) -> str:
    """«VLESS XHTTP», «NAIVE» — как `f"{proto.upper()} {utype.upper()}".strip()`."""
    return f"{proto.upper()} {utype.upper()}".strip()


def conn_label(conn: Connection) -> str:
    return proto_label(*proto_type(conn))


def parse_slot(proto: str, utype: str) -> tuple[Protocol, Transport | None]:
    """Из callback_data («vless», «xhttp» | «none» | «») в слот kvnBox."""
    protocol = Protocol(proto)
    if protocol is not Protocol.VLESS:
        return protocol, None
    return protocol, TRANSPORT_BY_TYPE.get(utype, Transport.WS)
