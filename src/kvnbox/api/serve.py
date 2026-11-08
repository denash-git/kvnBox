"""`kvnbox api serve`: постоянный канал для веб-меню лаунчера поверх одного SSH-подключения.

Построчный JSON. Запрос: {"id": "7", "action": "users.list", "args": {...}}.
Ответ: {"id": "7", "ok": true, "data": ...} или {"id": "7", "ok": false, "error": "..."};
промежуточный вывод долгих действий и поток журнала — {"id": "7", "event": "line", "data": "..."}.
Отмена потока: {"id": "8", "action": "api.cancel", "args": {"target": "7"}}.
Запросы выполняются параллельно; действия, меняющие данные, — по одному (общая блокировка).
Конец ввода (лаунчер закрыт, SSH оборвался) — выход.
"""

from __future__ import annotations

import json
import threading
from typing import IO, Any

from kvnbox import __version__
from kvnbox.api import API_VERSION
from kvnbox.api.actions import is_mutating, run_action
from kvnbox.services import ServiceError
from kvnbox.services.app import App

BOM = chr(0xFEFF)


class Server:
    def __init__(self, app: App, out: IO[str]) -> None:
        self.app = app
        self.out = out
        self.out_lock = threading.Lock()
        self.change_lock = threading.Lock()
        self.cancelled: set[str] = set()

    def send(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg, ensure_ascii=False)
        with self.out_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def handle(self, req: dict[str, Any]) -> None:
        rid = str(req.get("id", ""))
        name = str(req.get("action", ""))
        args = req.get("args") or {}
        if not isinstance(args, dict):
            self.send({"id": rid, "ok": False, "error": "args должны быть объектом"})
            return
        if name == "api.cancel":
            self.cancelled.add(str(args.get("target", "")))
            self.send({"id": rid, "ok": True, "data": None})
            return

        def emit(line: str) -> None:
            self.send({"id": rid, "event": "line", "data": line})

        def cancelled() -> bool:
            return rid in self.cancelled

        try:
            if is_mutating(name):
                with self.change_lock:
                    data = run_action(self.app, name, args, emit, cancelled)
            else:
                data = run_action(self.app, name, args, emit, cancelled)
            self.send({"id": rid, "ok": True, "data": data})
        except ServiceError as e:
            self.send({"id": rid, "ok": False, "error": str(e)})
        except Exception as e:
            self.send({"id": rid, "ok": False, "error": f"Внутренняя ошибка: {e}"})
        finally:
            self.cancelled.discard(rid)

    def run(self, inp: IO[str]) -> int:
        self.send({"id": None, "ready": True, "api": API_VERSION, "panel": __version__})
        for raw in inp:
            raw = raw.strip().lstrip(BOM)  # BOM от Windows перед первой строкой
            if not raw:
                continue
            try:
                req = json.loads(raw)
                if not isinstance(req, dict):
                    raise ValueError
            except ValueError:
                self.send({"id": None, "ok": False, "error": "неверный JSON"})
                continue
            threading.Thread(target=self.handle, args=(req,), daemon=True).start()
        return 0
