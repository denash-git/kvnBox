"""Диагностика: мониторинг, тесты скорости, пересоздание конфигураций."""

from __future__ import annotations

import asyncio
import json
import re
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from kvnbox.bot.common import edit, run, show
from kvnbox.bot.keyboards import (
    diagnostics_back_keyboard,
    diagnostics_menu_keyboard,
    iperf_running_keyboard,
    speedtest_menu_keyboard,
)
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier
from kvnbox.services.diagnostics import DiagnosticsService
from kvnbox.services.modules import NetbirdModule
from kvnbox.system.metrics import cpu_percent

router = Router()
IPERF_TIMEOUT = 180
_iperf_task: asyncio.Task[None] | None = None


def _service(app: App, unit: str) -> str:
    return "🟢 работает" if app.systemd.state(unit) == "active" else "🔴 не запущен"


def _netbird(app: App) -> str:
    return {
        "not_installed": "⚪ не установлен",
        "stopped": "🔴 остановлен",
        "needs_login": "🟡 требует регистрации",
        "connected": "🟢 подключен",
    }.get(NetbirdModule(app).connection_state(), "🔴 отключен")


def status_text(app: App) -> str:
    m = app.metrics
    a = m.cpu_times()
    time.sleep(0.1)
    cpu = float(cpu_percent(a, m.cpu_times()))
    mem = m.memory()
    disk = m.disk()
    disk_pct = round(disk.used * 100 / (disk.used + disk.free), 1) if disk.used + disk.free else 0
    secs = int(m.uptime_seconds())
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    uptime = ""
    if days > 0:
        uptime += f"{days} дн. "
    if hours > 0 or days > 0:
        uptime += f"{hours} ч. "
    uptime += f"{minutes} мин."
    mita = _service(app, "mita") if app.installed("mita") else "⚪ не установлен"
    return (
        "📈 <b>Мониторинг ресурсов и служб</b>\n\n"
        "⚙️ <b>Состояние служб:</b>\n"
        f"├─ Caddy: {_service(app, 'caddy')}\n"
        f"├─ sing-box: {_service(app, 'sing-box')}\n"
        f"├─ Mieru: {mita}\n"
        f"├─ NetBird: {_netbird(app)}\n"
        f"├─ Защита (Fail2ban): {'🟢 активна' if app.fail2ban.is_active() else '🔴 отключена'}\n"
        f"└─ TCP BBR: {'🟢 активен' if app.bbr.is_active() else '🔴 отключен'}\n"
        "\n🖥️ <b>Ресурсы сервера:</b>\n"
        f"├─ CPU: <code>{cpu}%</code>\n"
        f"├─ RAM: <code>{mem.exact_percent}%</code> ({mem.used_mb} / {mem.total_mb} MB)\n"
        f"├─ Диск (/): <code>{disk_pct}%</code> ({disk.used // 1024**3} / "
        f"{disk.total // 1024**3} GB)\n"
        f"└─ Аптайм: <code>{uptime}</code>\n"
    )


SPEEDTEST_TITLE = "🚀 <b>Тест скорости VPS → Интернет</b>\n\n"


def _speed_lines(dl: str, ul: str, ping: str, jitter: str | None, loss: str) -> str:
    """Короткий формат по просьбе пользователя (D-034): только числа, подписи англ."""
    lines = [
        f"📥 Download: <code>{dl} Mbps</code>",
        f"📤 Upload: <code>{ul} Mbps</code>",
        f"⚡️ Ping: <code>{ping} ms</code>",
    ]
    if jitter is not None:
        lines.append(f"      jitter: <code>{jitter} ms</code>")
    lines.append(f"📊 loss: <code>{loss}</code>")
    return SPEEDTEST_TITLE + "\n".join(lines)


def format_speedtest_result(raw: str) -> str:
    """Результат Ookla: JSON, иначе разбор текстового вывода."""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                ping = data.get("ping", {})
                loss = data.get("packetLoss")
                return _speed_lines(
                    str(round(data.get("download", {}).get("bandwidth", 0) * 8 / 1_000_000, 2)),
                    str(round(data.get("upload", {}).get("bandwidth", 0) * 8 / 1_000_000, 2)),
                    f"{ping.get('latency', 0.0):.2f}",
                    f"{ping.get('jitter', 0.0):.2f}",
                    f"{loss:.1f}%" if isinstance(loss, (int, float)) else "0.0%",
                )
            except Exception:
                pass

    def find(pattern: str) -> re.Match[str] | None:
        return re.search(pattern, raw, re.I)

    lat_m = find(r"(?:Idle Latency|Latency):\s*([\d\.]+)\s*ms\s*(?:\(jitter:\s*([\d\.]+)ms)?")
    dl_m, ul_m = find(r"Download:\s*([\d\.]+)\s*Mbps"), find(r"Upload:\s*([\d\.]+)\s*Mbps")
    loss_m = find(r"Packet Loss:\s*([\d\.]+)%")
    return _speed_lines(
        dl_m.group(1) if dl_m else "—",
        ul_m.group(1) if ul_m else "—",
        lat_m.group(1) if lat_m else "—",
        lat_m.group(2) if lat_m and lat_m.group(2) else None,
        loss_m.group(1) + "%" if loss_m else "0.0%",
    )


@router.message(Command("diag"))
@router.callback_query(F.data == "diag:menu")
async def diagnostics_menu(event: Message | CallbackQuery) -> None:
    await show(
        event,
        "📊 <b>Диагностика системы</b>\n\nВыберите нужный инструмент диагностики:",
        diagnostics_menu_keyboard(),
    )


@router.message(Command("status"))
@router.callback_query(F.data == "diag:resources")
async def diagnostics_resources(event: Message | CallbackQuery, app: App) -> None:
    await show(event, await run(status_text, app), diagnostics_back_keyboard())


@router.callback_query(F.data == "diag:speedtest")
async def speed_menu(cb: CallbackQuery) -> None:
    await edit(
        cb,
        (
            "🚀 <b>Тестирование скорости сети</b>\n\n"
            "Выберите тип замера скорости:\n\n"
            "• <b>VPS → Интернет:</b> официальный замер канала сервера до узлов Ookla Speedtest.\n"
            "• <b>Client ⇄ VPS (iperf3):</b> замер прямого канала между вашим ПК/смартфоном и сервером."
        ),
        speedtest_menu_keyboard(),
    )
    await cb.answer()


@router.callback_query(F.data.in_({"diag:speed_ookla", "diag:speed_run"}))
async def speed_ookla(cb: CallbackQuery, app: App) -> None:
    await edit(
        cb,
        "⏳ <b>Запуск теста скорости VPS → Интернет...</b>\n"
        "Пожалуйста, подождите 15-30 секунд, идет замер пропускной способности...",
    )
    await cb.answer()
    diag = DiagnosticsService(app)
    try:
        output = await run(diag.speedtest_json)
        text = format_speedtest_result(output)
    except ServiceError as e:
        text = f"❌ <b>Ошибка запуска Speedtest:</b>\n\n<code>{e}</code>"
    await edit(cb, text, speedtest_menu_keyboard())


async def _iperf_auto_stop(diag: DiagnosticsService) -> None:
    await asyncio.sleep(IPERF_TIMEOUT)
    await run(diag.iperf_stop)


@router.callback_query(F.data == "diag:speed_iperf")
async def speed_iperf(cb: CallbackQuery, app: App) -> None:
    global _iperf_task
    await edit(cb, "⏳ <b>Подготовка сервера iperf3...</b>")
    await cb.answer()
    diag = DiagnosticsService(app)
    if not app.installed("iperf3"):
        await edit(
            cb,
            "❌ <b>Не удалось установить iperf3 на сервере.</b>\n"
            "Установите вручную: <code>apt install iperf3</code>",
            speedtest_menu_keyboard(),
        )
        return
    try:
        await run(diag.iperf_start)
    except ServiceError as e:
        await edit(cb, f"❌ <b>Ошибка запуска iperf3:</b> {e}", speedtest_menu_keyboard())
        return
    if _iperf_task and not _iperf_task.done():
        _iperf_task.cancel()
    _iperf_task = asyncio.create_task(_iperf_auto_stop(diag))
    ip = await run(diag.public_ip) or "ВАШ_IP_VPS"
    await edit(
        cb,
        (
            "📶 <b>Замер скорости: Client ⇄ VPS (iperf3)</b>\n\n"
            "🟢 <b>Сервер iperf3 временно запущен на VPS!</b>\n"
            "<i>Служба и порт 5201 в фаерволе автоматически закроются через 3 минуты "
            "(или по кнопке «Остановить iperf3»).</i>\n\n"
            "💻 <b>Команды для терминала ПК (Windows / Mac / Linux):</b>\n"
            "• Скачивание (клиент → VPS):\n"
            f"<code>iperf3 -c {ip}</code>\n"
            "• Отдача (VPS → клиент):\n"
            f"<code>iperf3 -c {ip} -R</code>\n\n"
            "📱 <b>Приложения для смартфонов:</b>\n"
            "• Android: <b>Magic iPerf</b>\n"
            "• iOS: <b>HE.NET Network Tools</b>\n"
            f"Адрес сервера: <code>{ip}</code>, порт: <code>5201</code>"
        ),
        iperf_running_keyboard(),
    )


@router.callback_query(F.data == "diag:iperf_stop")
async def iperf_stop(cb: CallbackQuery, app: App) -> None:
    await run(DiagnosticsService(app).iperf_stop)
    await edit(
        cb,
        "📶 <b>Замер скорости: Client ⇄ VPS (iperf3)</b>\n\n"
        "⚪ <b>Сервер iperf3 остановлен.</b>\n"
        "Порт 5201 в фаерволе UFW успешно закрыт.",
        speedtest_menu_keyboard(),
    )
    await cb.answer("iperf3 остановлен, порт закрыт", show_alert=False)


@router.callback_query(F.data == "diag:recreate")
async def recreate(cb: CallbackQuery, app: App) -> None:
    await edit(cb, "⏳ <b>Перегенерация конфигурационных файлов...</b>")
    await cb.answer()
    res = await run(Applier(app).apply)
    if res.ok:
        text = (
            "✅ <b>Успешно!</b>\nВсе конфигурационные файлы пересозданы, службы успешно "
            "перезапущены."
        )
    else:
        text = f"❌ <b>Конфиги пересозданы, но служба не запустилась:</b>\n\n<code>{res.message}</code>"
    await edit(cb, text, diagnostics_back_keyboard())
