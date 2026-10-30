"""MCP-сервер статических инструментов с явной границей игрового канала."""

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .packages import PackageError, validate_package
from .sources import SourceCatalog, SourceError


LOCAL_TOOLS = (
    ("g1r_status", "Состояние сервера и граница игрового канала"),
    ("g1r_capabilities", "Каталог реализованных и недоступных групп"),
    ("g1r_find_source_files", "Поиск файлов официального Modkit"),
    ("g1r_search_source", "Буквальный поиск строк AngelScript"),
    ("g1r_read_source", "Ограниченное чтение исходника Modkit"),
    ("g1r_list_source_types", "Лексический поиск объявлений типов"),
    ("g1r_validate_package", "Структурная проверка официального пакета"),
)


def _tool_failure(error: SourceError | PackageError) -> ToolError:
    return ToolError(json.dumps({"code": error.code, "message": str(error)}, ensure_ascii=False))


def create_server(modkit_root: Path, workspace_root: Path, source_revision: str | None = None) -> MCPServer:
    """Создать сервер с двумя явно заданными read-only корнями."""
    if source_revision is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision):
        raise ValueError("Ревизия источников должна быть полным Git SHA либо отсутствовать")
    workspace_root = workspace_root.absolute()
    if not workspace_root.is_dir():
        raise ValueError("Рабочий каталог отсутствует")
    for component in (workspace_root, *workspace_root.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise ValueError("Рабочий каталог содержит ссылку или junction")
    workspace_root = workspace_root.resolve(strict=True)
    sources = SourceCatalog(modkit_root, revision=source_revision)
    server = MCPServer(
        "G1R MCP", version=__version__, log_level="WARNING",
        instructions=(
            "Локальные инструменты разработки официальных AngelScript-модов. "
            "Поиск и проверка пакета не являются компиляцией или игровым вызовом. "
            "Игровой канал не реализован. Не выводите работоспособность API из наличия исходника."
        ),
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def g1r_status() -> dict[str, Any]:
        """Узнать версию сервера и какие результаты не подтверждены в игре."""
        return {
            "server": {"name": "G1R MCP", "version": __version__, "transport": "stdio", "ready": True},
            "sources": {"kind": "official_modkit_static", "revision": source_revision,
                        "revision_source": "caller_label", "validation": "source_only"},
            "game": {
                "backend_implemented": False, "connected": False, "ready": False,
                "reason_code": "game_channel_not_implemented",
                "reason": "Официальный игровой канал не подтверждён; локальные инструменты доступны независимо от игры",
                "last_research": {
                    "date": "2026-10-30", "buildid": "26295476", "live_status": "not_checked_by_this_tool",
                    "source": "https://github.com/AlkimiaInteractive/G1R-Modkit/issues/1#issuecomment-6520702135",
                },
            },
        }

    @server.tool(annotations=read_only)
    def g1r_capabilities() -> dict[str, Any]:
        """Получить каталог локальных инструментов и отсутствующих игровых групп."""
        return {
            "catalog_revision": __version__,
            "catalog_revision_kind": "server_tool_contract",
            "local_tools": [
                {"name": name, "description": description, "implemented": True,
                 "available": True, "effects": "read_only", "validation": "local_only"}
                for name, description in LOCAL_TOOLS
            ],
            "game_groups": [
                {"name": group, "implemented": False, "available": False,
                 "reason_code": "game_channel_not_implemented"}
                for group in ("state", "objects", "observations", "actions", "compile_diagnostics", "hot_reload")
            ],
            "limitations": [
                "Исходники не покрывают все нативные bindings и не подтверждают вызовы в текущей игре",
                "Объявления типов найдены лексически; это не AST компилятора и не runtime-рефлексия",
                "Проверка пакета не загружает мод и не проверяет алгоритм сравнения версий загрузчика",
            ],
        }

    @server.tool(annotations=read_only)
    def g1r_find_source_files(query: str, limit: int = 50) -> dict[str, Any]:
        """Найти относительные пути .as в Script и примерах официального Modkit."""
        try:
            return sources.search_files(query, limit)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_search_source(query: str, limit: int = 50, case_sensitive: bool = False) -> dict[str, Any]:
        """Искать буквальный текст в официальных .as; вернуть ограниченные строки со ссылками."""
        try:
            return sources.search_text(query, limit, case_sensitive=case_sensitive)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_read_source(relative_path: str, start_line: int = 1, line_count: int = 100) -> dict[str, Any]:
        """Читать диапазон строк исходника по относительному пути разрешённого каталога."""
        try:
            return sources.read_file(relative_path, start_line, line_count)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_list_source_types(query: str = "", limit: int = 50) -> dict[str, Any]:
        """Найти лексические объявления class/struct/enum; не считать их компиляцией или reflection."""
        try:
            return sources.list_types(query, limit)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_validate_package(relative_path: str) -> dict[str, Any]:
        """Проверить GothicMod.json и структуру пакета в рабочем каталоге без загрузки в игру."""
        try:
            return validate_package(workspace_root, relative_path)
        except PackageError as error:
            raise _tool_failure(error) from None

    return server


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage: ", "Вызов: ", 1).replace("options:", "Параметры:")

        def error(self, message):
            self.exit(2, "Некорректные параметры G1R MCP; используйте --help\n")

    parser = RussianArgumentParser(description="G1R MCP: локальный сервер разработки модов через stdio", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--modkit-root", type=Path, required=True, help="Корень официального Modkit")
    parser.add_argument("--workspace-root", type=Path, required=True, help="Корень разрабатываемых пакетов")
    parser.add_argument("--source-revision", help="Полный Git SHA Modkit; при отсутствии ревизия неизвестна")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.modkit_root, args.workspace_root, args.source_revision)
    except (ValueError, OSError, SourceError) as error:
        print(f"Не удалось создать G1R MCP: {error}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
