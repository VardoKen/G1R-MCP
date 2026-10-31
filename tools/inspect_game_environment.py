"""CLI сверки явно указанной игры и Modkit; логика доступна из общего модуля."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from g1r_mcp.game_environment import (
    InspectionError, InspectionOptions, inspect_environment, parse_manifest,
)


class JsonArgumentParser(argparse.ArgumentParser):
    """Не выводить введённые пути и значения при ошибках аргументов."""

    def format_help(self):
        return super().format_help().replace("usage: ", "Вызов: ", 1)

    def error(self, message: str) -> None:
        raise InspectionError("invalid_arguments", "Некорректные параметры; используйте --help")


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(
        description="Сверить явно указанные Steam, игру и Modkit без изменения файлов",
        add_help=False, usage="%(prog)s [параметры]",
    )
    parser._optionals.title = "Параметры"
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--game-root", type=Path, required=True, help="Установочный каталог игры")
    parser.add_argument("--modkit-root", type=Path, required=True, help="Каталог официального Modkit")
    parser.add_argument("--steam-manifest", type=Path, required=True, help="Конкретный Steam appmanifest .acf")
    parser.add_argument("--hash-executable", action="store_true", help="Потоково вычислить SHA-256 известного игрового EXE")
    parser.add_argument("--max-list-entries", type=int, default=100, help="Предел элементов каждого списка различий; по умолчанию 100")
    try:
        result = inspect_environment(InspectionOptions(**vars(parser.parse_args(argv))))
    except InspectionError as error:
        result = {"ok": False, "error": {"code": error.code, "message": str(error)}}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
