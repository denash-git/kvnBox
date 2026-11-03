"""Точка входа `python -m kvnbox` / команда `kvnbox`.

Пока только версия; CLI появится на этапе 5.
"""

from __future__ import annotations

import sys

from kvnbox import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["--version"], ["-V"]):
        print(f"kvnBox {__version__}")
        return 0
    print("kvnBox: CLI ещё не реализован (этап 5). Используйте --version.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
