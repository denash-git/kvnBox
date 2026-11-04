from __future__ import annotations

import contextlib

import pytest

from kvnbox.core.models import Protocol
from kvnbox.tui.header import MAIN_TITLE
from kvnbox.tui.menu import Menu
from kvnbox.tui.term import DOWN, ENTER, UP, FakeTerminal, OutOfInput, strip_ansi
from kvnbox.tui.widgets import BACK, ExitMenu, Ui
from tests.conftest import Env

# ─── виджеты: вывод байт-в-байт как в Bash ───────────────────────────────────


def test_center_print_like_bash() -> None:
    t = FakeTerminal(width=100)
    Ui(t).center_print("\033[1;33mНастройки\033[0m")
    # (100 - 9) // 2 = 45 пробелов, цветовые коды в длину не входят
    assert t.output == [" " * 45 + "\033[1;33mНастройки\033[0m\n"]


def test_block_pad_minimum() -> None:
    assert Ui(FakeTerminal(width=100)).block_pad() == " " * 20
    assert Ui(FakeTerminal(width=40)).block_pad() == "  "


def test_select_menu_rendering_and_hint() -> None:
    t = FakeTerminal([ENTER], width=100, height=40)
    ui = Ui(t)
    assert ui.select_menu("T", ["Один", "Удалить всё", "Назад"], lambda _: None) == 0
    out = "".join(t.output)
    indent = " " * ((100 - 11) // 2)
    # активный пункт — инверсия, ширина дополнена до самого длинного
    assert f"{indent}\033[7m  Один         \033[0m\033[K\n" in out  # «  %b  » как в Bash
    # «Удалить» — красный
    assert f"{indent}  \033[1;31mУдалить всё\033[0m  \033[K\n" in out
    # «Назад» — по центру блока, жёлтый, с дополнительной пустой строкой
    assert f"\n\n{indent}     \033[1;33mНазад\033[0m     \033[K\n" in out
    assert "\033[40;1H\033[K" in out and "↑↓ — выбор" in out


def test_select_menu_navigation() -> None:
    def pick(keys: list[str]) -> int:
        return Ui(FakeTerminal(keys)).select_menu("T", ["a", "b", "c"], lambda _: None)

    assert pick([DOWN, DOWN, ENTER]) == 2
    assert pick([UP, ENTER]) == 2  # по кругу
    assert pick([DOWN, "q"]) == BACK
    with pytest.raises(ExitMenu):
        pick(["0"])


def test_danger_active_red_background() -> None:
    t = FakeTerminal([ENTER])
    Ui(t).select_menu("T", ["Удалить этот протокол"], lambda _: None)
    assert "\033[41;30m  Удалить этот протокол  \033[0m" in "".join(t.output)


# ─── меню целиком ────────────────────────────────────────────────────────────


def run(env: Env, keys: list[str]) -> FakeTerminal:
    term = FakeTerminal(keys)
    # выход по «0» (ExitMenu), по «q» из главного меню (обычный возврат)
    # или когда закончился сценарий клавиш (OutOfInput)
    with contextlib.suppress(ExitMenu, OutOfInput):
        Menu(env.app, term).main_menu()
    return term


def test_main_menu_header(env: Env) -> None:
    term = run(env, ["0"])
    first = term.screens()[1]
    assert MAIN_TITLE in first and "Домен:        tst.example.com" in first
    for label in ("TCP BBR:", "Caddy:", "sing-box:", "Mieru:", "SSH пароль:", "Защита:"):
        assert label in first
    assert "NetBird:" not in first  # не установлен — строки нет
    for item in (
        "Пользователи",
        "Настройки",
        "Модули",
        "Диагностика",
        "Бэкап и Восстановление",
        "Выход",
    ):
        assert item in first


def test_exit_item_returns(env: Env) -> None:
    term = FakeTerminal([UP, ENTER])  # «Выход» — последний пункт
    assert Menu(env.app, term).run() == 0


def test_create_user_through_menu(env: Env) -> None:
    keys = [
        ENTER,  # Пользователи
        DOWN,
        ENTER,  # Добавить нового пользователя
        "mama",  # никнейм
        DOWN,
        DOWN,
        ENTER,  # VLESS over XHTTP
        DOWN,
        DOWN,
        ENTER,  # «Завершить»
        "0",
    ]
    term = run(env, keys)
    user = env.app.users.get("mama")
    assert user is not None and user.connections[0].slot[1] is not None
    assert "Пользователь создан: mama" in term.text


def test_user_list_sorted_and_protocol_labels(env: Env) -> None:
    env.app.users.add_connection("zeta", Protocol.NAIVE)
    env.app.users.add_connection("Alpha", Protocol.MIERU)
    term = run(env, [ENTER, ENTER, "0"])
    screen = term.screens()[-2]  # последний — очистка экрана при выходе по «0»
    assert screen.index("Alpha") < screen.index("zeta")
    assert "Alpha    (Протоколы: Mieru)" in screen and "zeta     (Протоколы: Naive)" in screen


def test_delete_user_through_menu(env: Env) -> None:
    env.app.users.add_connection("bob", Protocol.NAIVE)
    keys = [
        ENTER,
        ENTER,
        ENTER,  # Пользователи → Список → bob
        DOWN,
        DOWN,
        DOWN,
        ENTER,  # [x] Удалить пользователя полностью
        "y",
        ENTER,
        "0",
    ]
    term = run(env, keys)
    assert env.app.users.get("bob") is None
    assert "Пользователь bob успешно удален!" in term.text


@pytest.mark.parametrize(
    "path",
    [
        [DOWN, ENTER],  # Настройки
        [DOWN, ENTER, DOWN, DOWN, DOWN, ENTER],  # Управление SSH
        [DOWN, DOWN, ENTER],  # Модули
        [DOWN, DOWN, ENTER, ENTER],  # Mieru
        [DOWN, DOWN, ENTER, DOWN, DOWN, ENTER],  # sing-box
        [DOWN, DOWN, DOWN, ENTER],  # Диагностика
        [DOWN, DOWN, DOWN, ENTER, DOWN, ENTER],  # Логи
        [DOWN, DOWN, DOWN, DOWN, ENTER],  # Бэкап
    ],
)
def test_sections_open_and_back(env: Env, path: list[str]) -> None:
    term = run(env, [*path, "q", "q", "q", "0"])
    assert "Traceback" not in term.text


def test_set_backup_password_through_menu(env: Env) -> None:
    keys = [
        DOWN,
        DOWN,
        DOWN,
        DOWN,
        ENTER,  # Бэкап
        DOWN,
        DOWN,
        ENTER,  # Задать пароль
        "secret",
        ENTER,
        "0",
    ]
    term = run(env, keys)
    assert env.app.settings.load().backup.password == "secret"
    assert "Текущий пароль для отправки в Telegram: не задан" in term.text


def test_strip_ansi() -> None:
    assert strip_ansi("\033[1;31mx\033[0m") == "x"
