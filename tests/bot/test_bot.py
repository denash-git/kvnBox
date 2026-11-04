from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from kvnbox.bot import keyboards as kb
from kvnbox.bot.handlers import services, settings, start, status, users
from kvnbox.core.models import Protocol, Transport
from kvnbox.system.runner import Result
from tests.bot import stubs
from tests.conftest import Env


def go(coro: Coroutine[Any, Any, Any]) -> list[dict[str, Any]]:
    stubs.REC.clear()
    asyncio.run(coro)
    return list(stubs.REC)


def last_edit(rec: list[dict[str, Any]]) -> dict[str, Any]:
    return [r for r in rec if r["op"] in ("edit_text", "answer")][-1]


# ─── клавиатуры ──────────────────────────────────────────────────────────────


def test_align_button_text() -> None:
    text = kb.align_button_text("👥 Пользователи")
    assert text.startswith("👥 Пользователи ") and text.endswith("​")
    long = "Очень-очень длинная надпись на кнопке"
    assert kb.align_button_text(long) == long


def test_users_list_sorted_two_per_row(env: Env) -> None:
    for name in ("zeta", "alpha", "mid"):
        env.app.users.add_connection(name, Protocol.NAIVE)
    markup = kb.users_list_keyboard(env.app.users.all())
    rows = [[b.text for b in row] for row in markup.inline_keyboard]
    assert rows[0] == ["👤 alpha", "👤 mid"] and rows[1] == ["👤 zeta"]
    assert rows[-2] == ["➕ Добавить пользователя"] and rows[-1] == ["« Главное меню"]


def test_user_info_keyboard_legacy_callbacks(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.VLESS, Transport.HTTPUPGRADE)
    env.app.users.add_connection("mama", Protocol.NAIVE)
    rows = kb.user_info_keyboard(env.app.users.get("mama")).inline_keyboard  # type: ignore[arg-type]
    assert rows[0][0].text.startswith("🟢 VLESS XHTTP")
    assert rows[0][0].callback_data == "user:proto:manage:mama:vless:xhttp"
    assert rows[1][0].callback_data == "user:proto:manage:mama:naive:none"
    assert [b.text for b in rows[2]] == ["➕ Протокол", "🌀 Подписка"]


def test_speedtest_button_has_handler() -> None:
    data = kb.speedtest_menu_keyboard().inline_keyboard[0][0].callback_data
    assert data == "diag:speed_ookla"  # в TransferBox было «diag:speed_run» без обработчика


# ─── обработчики ─────────────────────────────────────────────────────────────


def test_main_menu() -> None:
    rec = go(start.main_menu(stubs.callback("menu:main")))
    assert last_edit(rec)["text"].startswith("📦 <b>kvnBox v. ")
    assert [row[0][1] for row in last_edit(rec)["kb"]] == [
        "user:list",
        "diag:menu",
        "settings:menu",
    ]


def test_user_info_and_proto_manage(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.VLESS, Transport.GRPC)
    rec = go(users.user_info(stubs.callback("user:info:mama"), env.app, stubs.state()))
    assert "<code>mama</code>\n          🟢 Активен" in last_edit(rec)["text"]
    rec = go(users.proto_manage(stubs.callback("user:proto:manage:mama:vless:grpc"), env.app))
    assert "Конфигурация: <code>VLESS GRPC</code>" in last_edit(rec)["text"]
    rec = go(users.user_info(stubs.callback("user:info:ghost"), env.app, stubs.state()))
    assert rec == [{"op": "alert", "text": "Пользователь не найден.", "show_alert": True}]


def test_proto_toggle_applies(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.NAIVE)
    rec = go(users.proto_toggle(stubs.callback("user:proto:toggle:mama:naive:none:0"), env.app))
    assert {"op": "alert", "text": "✓ Успешно выполнено!", "show_alert": True} in rec
    assert not env.app.users.get("mama").connections[0].enabled  # type: ignore[union-attr]
    assert "🔴 Выключен" in last_edit(rec)["text"]


def test_add_user_flow(env: Env) -> None:
    st = stubs.state()
    go(users.new_step1(stubs.callback("user:new:step1"), st))
    rec = go(users.new_nickname(stubs.message("Mama"), st))
    assert "Никнейм: <code>mama</code>" in last_edit(rec)["text"]  # приводится к нижнему регистру
    rec = go(users.new_proto(stubs.callback("user:new:proto:vless"), env.app, st))
    assert [r[0][1] for r in last_edit(rec)["kb"]][:3] == [
        "user:new:type:ws",
        "user:new:type:grpc",
        "user:new:type:xhttp",
    ]
    go(users.new_type(stubs.callback("user:new:type:xhttp"), env.app, st))
    rec = go(users.new_confirm(stubs.callback("user:new:confirm:yes"), env.app, st))
    assert "🎉 Конфигурация VLESS" in last_edit(rec)["text"]
    assert env.app.users.get("mama").connections[0].transport is Transport.HTTPUPGRADE  # type: ignore[union-attr]


def test_add_existing_slot_reports_error(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.NAIVE)
    st = stubs.state()
    asyncio.run(st.update_data(nickname="mama", protocol="naive", type=""))
    rec = go(users.new_confirm(stubs.callback("user:new:confirm:yes"), env.app, st))
    assert "Не удалось добавить" in last_edit(rec)["text"]  # TransferBox писал «успешно»


def test_bad_nickname(env: Env) -> None:
    rec = go(users.new_nickname(stubs.message("мама!"), stubs.state()))
    assert "Неверный формат никнейма" in last_edit(rec)["text"]


def test_subscription_links_send_message_and_qr(env: Env) -> None:
    env.app.users.add_connection("mama", Protocol.MIERU)
    env.runner.on("qrencode", result=Result(0))

    def fake_qr(self: object, text: str, path: Any) -> Any:
        path.write_bytes(b"PNG")
        return path

    from kvnbox.services.users import UserService

    orig = UserService.qr_png
    UserService.qr_png = fake_qr  # type: ignore[method-assign]
    try:
        rec = go(users.proto_links(stubs.callback("user:proto:links:mama:mieru:none"), env.app))
    finally:
        UserService.qr_png = orig  # type: ignore[method-assign]
    ops = [r["op"] for r in rec]
    assert ops == ["alert", "send_message", "send_photo", "send_message", "send_photo"]
    assert "mierus://" in rec[1]["text"] and "Sing-box JSON" in rec[3]["text"]


def test_services_and_diag(env: Env) -> None:
    rec = go(services.services_confirm(stubs.callback("services:confirm:all")))
    assert last_edit(rec)["text"] == "❓ Вы действительно хотите перезапустить все прокси-службы?"
    rec = go(services.services_run(stubs.callback("services:run:all"), env.app))
    assert "🟢 <b>Успешно!</b>" in last_edit(rec)["text"]
    assert ["systemctl", "restart", "mita"] in env.runner.calls  # mita установлен
    rec = go(services.services_run(stubs.callback("services:run:rm -rf"), env.app))
    assert not [r for r in rec if r["op"] == "edit_text"]


def test_resources_text(env: Env) -> None:
    p = env.paths.proc
    p.mkdir(parents=True)
    (p / "stat").write_text("cpu  10 0 0 90 0 0 0 0\n")
    (p / "meminfo").write_text("MemTotal: 1000000 kB\nMemFree: 100 kB\nMemAvailable: 500000 kB\n")
    (p / "uptime").write_text("90061.5 0\n")
    from kvnbox.system.metrics import Disk, Metrics

    orig = Metrics.disk
    Metrics.disk = lambda self: Disk(10 * 1024**3, 4 * 1024**3, 6 * 1024**3)  # type: ignore[method-assign]
    try:
        text = status.status_text(env.app)
    finally:
        Metrics.disk = orig  # type: ignore[method-assign]
    assert "├─ RAM: <code>50.0%</code>" in text and "Аптайм: <code>1 дн. 1 ч. 1 мин.</code>" in text
    assert "├─ Mieru: " in text and "⚪ не установлен" in text  # NetBird


def test_speedtest_result_format() -> None:
    raw = (
        '{"server":{"name":"ISP","location":"Moscow","id":1},"isp":"Host",'
        '"ping":{"latency":3.21,"jitter":0.5},"download":{"bandwidth":12500000},'
        '"upload":{"bandwidth":6250000},"packetLoss":0}'
    )
    text = status.format_speedtest_result(raw)
    assert text.splitlines()[2:] == [
        "📥 Download: <code>100.0 Mbps</code>",
        "📤 Upload: <code>50.0 Mbps</code>",
        "⚡️ Ping: <code>3.21 ms</code>",
        "      jitter: <code>0.50 ms</code>",
        "📊 loss: <code>0.0%</code>",
    ]
    text = status.format_speedtest_result(
        "Idle Latency: 7.96 ms (jitter: 0.09ms)\nDownload: 456.39 Mbps\nUpload: 566.46 Mbps\n"
    )
    assert "📥 Download: <code>456.39 Mbps</code>" in text
    assert "jitter: <code>0.09 ms</code>" in text


def test_no_emoji_arrow_in_bot() -> None:
    """«↔» Telegram рисует картинкой-эмодзи — в боте только текстовая стрелка."""
    assert "⇄" in kb.speedtest_menu_keyboard().inline_keyboard[1][0].text


def test_settings_backup_toggle_and_time(env: Env) -> None:
    rec = go(settings.backup_toggle(stubs.callback("settings:backup_toggle"), env.app))
    assert rec[0]["text"].startswith("Автобэкапы успешно включены")
    assert (env.paths.etc / "cron.d" / "kvnbox").exists()
    st = stubs.state()
    rec = go(settings.backup_time_msg(stubs.message("25:00"), env.app, st))
    assert "Недопустимые значения" in last_edit(rec)["text"]
    rec = go(settings.backup_time_msg(stubs.message("03:30"), env.app, st))
    assert "<code>03:30</code>" in last_edit(rec)["text"]
    assert "30 3 * * *" in (env.paths.etc / "cron.d" / "kvnbox").read_text()


def test_ssh_toggle_requires_key(env: Env) -> None:
    env.runner.on("sshd", "-T", result=Result(0, "port 22\npasswordauthentication yes\n"))
    rec = go(settings.ssh_toggle(stubs.callback("ssh:toggle:key"), env.app))
    assert rec == [
        {
            "op": "alert",
            "text": "Сначала сгенерируйте ключ в меню консоли (menu)!",
            "show_alert": True,
        }
    ]


def test_auth_middleware(env: Env) -> None:
    from aiogram.types import Update

    from kvnbox.bot.main import AuthMiddleware

    with env.app.settings.transaction() as s:
        s.telegram.chat_id = str(stubs.CHAT_ID)
    mw = AuthMiddleware(env.app)
    called: list[int] = []

    async def handler(event: Any, data: Any) -> None:
        called.append(1)

    ok = Update.model_construct(update_id=1, callback_query=stubs.callback("menu:main"))
    asyncio.run(mw(handler, ok, {}))
    stranger = stubs.callback("menu:main")
    object.__setattr__(stranger, "from_user", stranger.from_user.model_copy(update={"id": 1}))
    asyncio.run(mw(handler, Update.model_construct(update_id=2, callback_query=stranger), {}))
    assert called == [1]


@pytest.mark.parametrize("data", ["diag:speed_ookla", "diag:speed_run"])
def test_speed_ookla_handlers_registered(data: str) -> None:
    from aiogram.types import CallbackQuery

    handlers = list(status.router.callback_query.handlers)
    matched = []
    for h in handlers:
        for f in h.filters or []:
            magic = getattr(f, "magic", None)
            if magic is not None and magic.resolve(CallbackQuery.model_construct(data=data)):
                matched.append(h.callback.__name__)
    assert "speed_ookla" in matched
