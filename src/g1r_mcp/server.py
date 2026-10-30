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
from .native_client import NativeClient, NativeError
from .workspace import PackageWorkspace, WorkspaceError
from .snapshots import SnapshotError, compare_snapshots
from .development import DevelopmentError, DevelopmentService
from .patches import PatchError, ScriptPatchBuilder


LOCAL_TOOLS = (
    ("g1r_status", "Состояние сервера и граница игрового канала"),
    ("g1r_capabilities", "Каталог реализованных и недоступных групп"),
    ("g1r_find_source_files", "Поиск файлов официального Modkit"),
    ("g1r_search_source", "Буквальный поиск строк AngelScript"),
    ("g1r_read_source", "Ограниченное чтение исходника Modkit"),
    ("g1r_read_source_exact", "Полный текст исходника с сохранением BOM, переводов строк и SHA-256"),
    ("g1r_list_source_types", "Лексический поиск объявлений типов"),
    ("g1r_validate_package", "Структурная проверка официального пакета"),
    ("g1r_build_script_patch", "Unified diff из проверенных байтов Modkit без изменения файлов"),
)
WORKSPACE_TOOLS = (
    ("g1r_create_package", "Создать официальный пакет в выбранной рабочей базе", "local_write"),
    ("g1r_list_package_files", "Список дескриптора и исходников рабочего пакета", "read_only"),
    ("g1r_read_package_file", "Чтение рабочего файла с SHA-256 для проверки изменений", "read_only"),
    ("g1r_read_package_file_exact", "Точный текст рабочего файла для изменения без нормализации", "read_only"),
    ("g1r_write_package_file", "Запись исходника или дескриптора с проверкой конфликта", "local_write"),
)
NATIVE_TOOLS = (
    ("g1r_native_status", "Живое состояние экспериментального нативного канала"),
    ("g1r_native_enable", "Включить очередь игрового Tick в уже загруженной DLL"),
    ("g1r_find_objects", "Поиск объектов текущего процесса игры"),
    ("g1r_object_properties", "Чтение отражённых скалярных свойств"),
    ("g1r_object_functions", "Список отражённых функций объекта"),
    ("g1r_invoke", "Один явный вызов отражённой функции"),
    ("g1r_snapshot", "Скопировать выбранные свойства объектов на одном Tick"),
    ("g1r_watch_start", "Начать ограниченное наблюдение за выбранными свойствами"),
    ("g1r_watch_read", "Прочитать события наблюдения и сведения о потерях"),
    ("g1r_watch_stop", "Остановить наблюдение и подтвердить прекращение чтений"),
    ("g1r_watch_forget", "Освободить завершённое наблюдение"),
)


def _tool_failure(error: SourceError | PackageError | WorkspaceError | SnapshotError | PatchError) -> ToolError:
    return ToolError(json.dumps({"code": error.code, "message": str(error)}, ensure_ascii=False))


def create_server(modkit_root: Path, workspace_root: Path, source_revision: str | None = None,
                  native_port: int = 27123, game_root: Path | None = None) -> MCPServer:
    """Создать сервер с каталогом Modkit и отдельной рабочей базой пакетов."""
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
    patch_builder = ScriptPatchBuilder(modkit_root, revision=source_revision)
    workspace = PackageWorkspace(workspace_root, protected_roots=[modkit_root])
    native = NativeClient(port=native_port)
    development = None
    def development_service() -> DevelopmentService:
        nonlocal development
        if development is None:
            development = DevelopmentService(repository_root=Path(__file__).resolve().parents[2],
                                             workspace_root=workspace_root, modkit_root=modkit_root,
                                             game_root=game_root or Path(r"E:\SteamLibrary\steamapps\common\Gothic 1 Remake"))
        return development
    server = MCPServer(
        "G1R MCP", version=__version__, log_level="WARNING",
        instructions=(
            "Локальные инструменты разработки официальных AngelScript-модов. "
            "Поиск и проверка пакета не являются компиляцией или игровым вызовом. "
            "Игровые операции используют экспериментальную DLL только при подключении. "
            "Вызовы g1r_invoke могут менять игру; не повторяйте их после таймаута. "
            "Не выводите работоспособность API из наличия исходника."
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
                "backend_implemented": True, "connection_checked": False, "ready": False,
                "reason_code": "use_native_status_for_live_state",
                "reason": "Для текущего состояния DLL вызовите g1r_native_status; локальные инструменты независимы",
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
            "native_tools": [{"name": name, "description": description, "implemented": True,
                              "read_only_hint": name in {"g1r_native_status", "g1r_find_objects", "g1r_object_properties", "g1r_object_functions", "g1r_snapshot", "g1r_watch_read"},
                              "availability": "requires_live_native_status"} for name, description in NATIVE_TOOLS],
            "workspace_tools": [
                {"name": name, "description": description, "implemented": True,
                 "available": True, "effects": effects, "validation": "local_only"}
                for name, description, effects in WORKSPACE_TOOLS
            ],
            "game_groups": [
                {"name": group, "implemented": False, "available": False,
                 "reason_code": "confirmed_operation_unavailable"}
                for group in ("compile_diagnostics", "arbitrary_angelscript")
            ],
            "development_tools": {"implemented": True, "availability": "requires_windows_and_configured_game",
                                  "operations": ["recipe", "processes", "start", "stop", "native_load"],
                                  "launch_result": "launch_requested", "compiler_result": "requires_runtime_probe"},
            "hot_reload": {"mechanism_confirmed": True, "method": "file_watch_development_mode",
                           "scope": "function_bodies", "runtime_result_requires_verification": True},
            "observations": {"implemented": True, "availability": "requires_live_native_status",
                             "source": "selected_reflected_properties_on_existing_tick",
                             "ownership": "native_tcp_session", "world_generation_confirmed": False},
            "snapshot_comparison": {"implemented": True, "effects": "read_only",
                                    "validation": "client_supplied_copies"},
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    def g1r_create_package(relative_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Создать новый GothicMod.json и пустой Script в рабочей базе; не загружает мод."""
        try:
            return workspace.create_package(relative_path, metadata)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_list_package_files(relative_path: str, limit: int = 100) -> dict[str, Any]:
        """Получить ограниченный список дескриптора, .as исходников и ScriptPatches/*.patch."""
        try:
            return workspace.list_package_files(relative_path, limit)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_read_package_file(relative_path: str, file_path: str, start_line: int = 1,
                              line_count: int = 100) -> dict[str, Any]:
        """Прочитать UTF-8 рабочий файл и SHA-256 его полных байтов для последующей записи."""
        try:
            return workspace.read_package_file(relative_path, file_path, start_line, line_count)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    def g1r_write_package_file(relative_path: str, file_path: str, content: str,
                               expected_sha256: str | None = None) -> dict[str, Any]:
        """Записать рабочий файл: замена требует SHA-256, без хеша создаётся только новый файл."""
        try:
            return workspace.write_package_file(relative_path, file_path, content, expected_sha256)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_build_script_patch(source_path: str, replacement_content: str,
                                expected_source_sha256: str, context_lines: int = 3) -> dict[str, Any]:
        """Создать unified diff из проверенных исходных байтов Modkit без изменения файлов."""
        try:
            return patch_builder.build_script_patch(source_path, replacement_content,
                                                    expected_source_sha256, context_lines)
        except PatchError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_read_source_exact(relative_path: str) -> dict[str, Any]:
        """Прочитать полный UTF-8 исходник до 1 МиБ для редактуры; сохраняет BOM, CRLF и EOF."""
        try:
            return sources.read_file_exact(relative_path)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_read_package_file_exact(relative_path: str, file_path: str) -> dict[str, Any]:
        """Прочитать рабочий файл целиком без изменения байтов при decode/encode UTF-8."""
        try:
            return workspace.read_package_file_exact(relative_path, file_path)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    def native_request(operation: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return native.request(operation, params)
        except NativeError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error),
                                       "may_have_executed": error.may_have_executed,
                                       "request_id": error.request_id, "effect": error.details}, ensure_ascii=False)) from None

    def development_request(operation: str, **params: Any) -> dict[str, Any]:
        try:
            return getattr(development_service(), operation)(**params)
        except DevelopmentError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error),
                                       "outcome_unknown": error.outcome_unknown,
                                       "retry": "only_explicit_new_request"}, ensure_ascii=False)) from None

    @server.tool(annotations=read_only)
    def g1r_development_recipe(relative_package: str) -> dict[str, Any]:
        """Проверить полный манифест рабочего пакета, процессы и SHA-256 DLL без запуска игры."""
        return development_request("recipe", relative_package=relative_package)

    @server.tool(annotations=read_only)
    def g1r_development_processes() -> dict[str, Any]:
        """Прочитать точные PID, времена создания и пути процессов выбранной игры."""
        return development_request("process_state")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_development_start(relative_package: str, expected_manifest_sha256: str,
                               restart: bool = False,
                               expected_process: dict[str, Any] | None = None) -> dict[str, Any]:
        """Запросить development-запуск проверенного пакета; restart требует точную идентичность процесса."""
        return development_request("start", relative_package=relative_package,
                                   expected_manifest_sha256=expected_manifest_sha256,
                                   restart=restart, expected_process=expected_process)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_development_stop(expected_process: dict[str, Any]) -> dict[str, Any]:
        """Закрыть только подтверждённый экземпляр игры через удерживаемые HANDLE."""
        return development_request("stop", expected_process=expected_process)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_native_load(expected_process: dict[str, Any], expected_dll_sha256: str) -> dict[str, Any]:
        """Загрузить DLL в проверенный процесс с проверкой хеша; неопределённый результат не повторяется."""
        return development_request("load_native", expected_process=expected_process,
                                   expected_dll_sha256=expected_dll_sha256)

    @server.tool(annotations=read_only)
    def g1r_native_status() -> dict[str, Any]:
        """Проверить DLL в текущей игре; не запускает игру или загрузчик."""
        return native_request("status", {})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def g1r_native_enable() -> dict[str, Any]:
        """Установить перехват Tick в уже загруженной DLL; выполнять после появления главного меню."""
        return native_request("enable_game", {})

    @server.tool(annotations=read_only)
    def g1r_find_objects(query: str = "", class_query: str = "", limit: int = 50,
                         exact_path: str = "") -> dict[str, Any]:
        """Найти UObject по части имени/класса на игровом Tick; вернуть непрозрачные ссылки."""
        params = {"query": query, "class_query": class_query, "limit": limit}
        if exact_path:
            params["exact_path"] = exact_path
        return native_request("objects", params)

    @server.tool(annotations=read_only)
    def g1r_object_properties(handle: str, limit: int = 50) -> dict[str, Any]:
        """Читать поддержанные скалярные свойства проверенной ссылки объекта."""
        return native_request("properties", {"handle": handle, "limit": limit})

    @server.tool(annotations=read_only)
    def g1r_object_functions(handle: str, limit: int = 50) -> dict[str, Any]:
        """Получить отражённые UFunction объекта и размеры их параметров."""
        return native_request("functions", {"handle": handle, "limit": limit})

    @server.tool(annotations=read_only)
    def g1r_snapshot(targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Скопировать свойства до 16 объектов на одном Tick; это последовательные чтения."""
        return native_request("snapshot", {"targets": targets})

    @server.tool(annotations=read_only)
    def g1r_compare_snapshots(before: dict[str, Any], after: dict[str, Any],
                              limit: int = 100) -> dict[str, Any]:
        """Сравнить ограниченные копии; исходный runtime и происхождение клиентских данных не проверяет."""
        try:
            return compare_snapshots(before, after, limit)
        except SnapshotError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    def g1r_watch_start(handle: str, properties: list[str], duration_ms: int,
                         interval_ms: int, capacity: int = 32) -> dict[str, Any]:
        """Наблюдать выбранные свойства на Tick с явными сроком, интервалом и ёмкостью буфера."""
        return native_request("watch_start", {"handle": handle, "properties": properties,
                              "duration_ms": duration_ms, "interval_ms": interval_ms,
                              "capacity": capacity})

    @server.tool(annotations=read_only)
    def g1r_watch_read(watch_id: str, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        """Прочитать события после seq без удаления; пропуски и переполнение обозначены явно."""
        return native_request("watch_read", {"watch_id": watch_id, "after_seq": after_seq, "limit": limit})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=True, openWorldHint=False))
    def g1r_watch_stop(watch_id: str) -> dict[str, Any]:
        """Прекратить последующие чтения на игровом потоке; финальные события сохраняются."""
        return native_request("watch_stop", {"watch_id": watch_id})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    def g1r_watch_forget(watch_id: str) -> dict[str, Any]:
        """Освободить уже завершённое наблюдение текущей сессии."""
        return native_request("watch_forget", {"watch_id": watch_id})

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
    def g1r_invoke(handle: str, function: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Явно вызвать UFunction со скалярными параметрами; эффект зависит от функции, повтор запрещён."""
        return native_request("invoke", {"handle": handle, "function": function, "arguments": arguments or {}})

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
    parser.add_argument("--native-port", type=int, default=27123, help="Порт localhost экспериментальной DLL")
    parser.add_argument("--game-root", type=Path, help="Корень выбранной игры для команд development-запуска")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.modkit_root, args.workspace_root, args.source_revision, args.native_port, args.game_root)
    except (ValueError, OSError, SourceError, NativeError) as error:
        print(f"Не удалось создать G1R MCP: {error}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
