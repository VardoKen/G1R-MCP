"""MCP-сервер статических инструментов с явной границей игрового канала."""

import argparse
import json
from pathlib import Path
import re
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer, Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import __version__
from .packages import PackageError, validate_package
from .sources import SourceCatalog, SourceError
from .native_client import NativeClient, NativeError
from pydantic import StrictBool, StrictStr
from .workspace import PackageWorkspace, WorkspaceError
from .snapshots import SnapshotError, compare_snapshots
from .development import DevelopmentError, DevelopmentService
from .patches import PatchError, ScriptPatchBuilder
from .session_ownership import SessionOwnership
from .compile_diagnostics import read_report


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
    ("g1r_compare_snapshots", "Сравнить копии снимков без игровых вызовов"),
)
WORKSPACE_TOOLS = (
    ("g1r_create_package", "Создать официальный пакет в выбранной рабочей базе", "local_write"),
    ("g1r_create_package_directory", "Создать каталоги Script или ScriptPatches внутри рабочего пакета", "local_write"),
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
    ("g1r_read_array", "Ограниченное чтение страницы отражённого массива"),
    ("g1r_read_struct", "Чтение трёх отражённых числовых компонентов структуры"),
    ("g1r_object_functions", "Список отражённых функций объекта"),
    ("g1r_invoke", "Один явный вызов отражённой функции"),
    ("g1r_snapshot", "Скопировать выбранные свойства объектов на одном Tick"),
    ("g1r_watch_start", "Начать ограниченное наблюдение за выбранными свойствами"),
    ("g1r_watch_read", "Прочитать события наблюдения и сведения о потерях"),
    ("g1r_watch_stop", "Остановить наблюдение и подтвердить прекращение чтений"),
    ("g1r_watch_forget", "Освободить завершённое наблюдение"),
)
DEVELOPMENT_TOOLS = (
    "g1r_session_status", "g1r_session_acquire", "g1r_session_release", "g1r_compile_diagnostics",
    "g1r_development_recipe", "g1r_development_processes", "g1r_development_start",
    "g1r_development_stop", "g1r_native_load",
)


def _tool_failure(error: SourceError | PackageError | WorkspaceError | SnapshotError | PatchError) -> ToolError:
    return ToolError(json.dumps({"code": error.code, "message": str(error)}, ensure_ascii=False))


def create_server(modkit_root: Path, workspace_root: Path, source_revision: str | None = None,
                  native_port: int = 27123, game_root: Path | None = None, *, transport: str = "stdio") -> MCPServer:
    """Создать сервер с каталогом Modkit и отдельной рабочей базой пакетов."""
    if source_revision is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision):
        raise ValueError("Ревизия источников должна быть полным Git SHA либо отсутствовать")
    if transport not in ("stdio", "streamable-http"):
        raise ValueError("Транспорт должен быть stdio или streamable-http")
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
    native_lock = threading.Lock()
    development_lock = threading.Lock()
    configured_game_root = game_root or Path(r"E:\SteamLibrary\steamapps\common\Gothic 1 Remake")
    development = None
    ownership = SessionOwnership()
    local_owner = object()

    def request_owner(ctx: Context | None) -> Any:
        if ctx is None:
            return local_owner
        try:
            if transport == "streamable-http":
                request = ctx.request_context.request
                session_id = request.headers.get("mcp-session-id") if request is not None else None
                if session_id:
                    return "http:" + session_id
                raise DevelopmentError("session_identity_unavailable", "HTTP-запрос не содержит идентичность MCP-сессии")
            return ctx.session
        except ValueError:
            return local_owner
    def development_service() -> DevelopmentService:
        nonlocal development
        with development_lock:
            if development is None:
                development = DevelopmentService(repository_root=Path(__file__).resolve().parents[2],
                                                 workspace_root=workspace_root, modkit_root=modkit_root,
                                                 game_root=configured_game_root)
            return development

    @asynccontextmanager
    async def server_lifespan(_server: MCPServer):
        """Общие ресурсы закрываются при остановке сервера, а не HTTP-сессии."""
        try:
            yield
        finally:
            with native_lock:
                current_native = native
            with development_lock:
                current_development = development
            try:
                current_native.close()
            finally:
                if current_development is not None:
                    current_development.close()
    server = MCPServer(
        "G1R MCP", version=__version__, log_level="WARNING",
        lifespan=server_lifespan,
        instructions=(
            "Мост разработки официальных AngelScript-пакетов GothicMod.json; это не UE4SS/Lua. "
            "Начните с g1r_status и g1r_capabilities: они не проверяют живую игру. "
            "Для нового development-сеанса: g1r_development_recipe → g1r_development_processes → "
            "явно разрешённый g1r_development_start с manifest SHA-256 → g1r_development_processes → "
            "g1r_native_load с точными PID/creation_time/executable и SHA-256 DLL → g1r_native_status. "
            "Если Tick не установлен, явно вызовите g1r_native_enable и проверьте ready через g1r_native_status; "
            "запрос Steam и загрузка DLL сами по себе не подтверждают компиляцию или готовность Tick. "
            "В уже работающем мосте начинайте с native_status, затем g1r_find_objects и "
            "g1r_object_functions перед явным g1r_invoke; завершайте свой сеанс g1r_development_stop. "
            "Один TCP-клиент владеет ссылками и наблюдениями; после reconnect найдите объекты заново. "
            "Редактируйте полные байты через g1r_read_source_exact/g1r_read_package_file_exact и SHA-256; "
            "построчные чтения нормализуют текст. Поиск и valid_structure не являются компиляцией. "
            "ScriptPatches создаются как diff, но их применение в игре не подтверждено. "
            "Старт, остановка, загрузка DLL и invoke меняют внешнее состояние: не повторяйте операцию "
            "автоматически после outcome_unknown, may_have_executed или do_not_retry. "
            "Не выводите работоспособность игрового API из наличия исходника."
        ),
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def g1r_status() -> dict[str, Any]:
        """Узнать версию сервера и какие результаты не подтверждены в игре."""
        return {
            "server": {"name": "G1R MCP", "version": __version__, "transport": transport, "ready": True},
            "sources": {"kind": "official_modkit_static", "root": str(sources.root), "revision": source_revision,
                        "revision_source": "caller_label", "validation": "source_only"},
            "workspace": {"root": str(workspace.root), "format": "GothicMod.json/Script/ScriptPatches",
                          "installed_game_deployment": False},
            "game_session": ownership.status(),
            "game": {
                "backend_implemented": True, "connection_checked": False, "ready": False, "ready_known": False,
                "configured_root": str(configured_game_root.absolute()),
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
                                  "operations": list(DEVELOPMENT_TOOLS),
                                  "launch_result": "launch_requested", "compiler_result": "requires_runtime_probe"},
            "compiler_reports": {"tool": "g1r_compile_diagnostics", "implemented": True,
                                 "source": "caller_selected_workspace_file", "compiler_api_available": False,
                                 "absence_of_errors_proves_compile": False},
            "bootstrap": {
                "game_sequence": [
                    {"tool": "g1r_development_recipe", "condition": "Проверить выбранный рабочий пакет и получить manifest/DLL SHA-256"},
                    {"tool": "g1r_development_processes", "condition": "Определить исходные PID/creation_time/executable"},
                    {"tool": "g1r_development_start", "condition": "Только явно выбранный запуск; restart требует исходную идентичность"},
                    {"tool": "g1r_development_processes", "condition": "Получить идентичность запущенного внутреннего игрового EXE"},
                    {"tool": "g1r_native_load", "condition": "Одна загрузка с ожидаемыми процессом и SHA-256 DLL"},
                    {"tool": "g1r_native_status", "condition": "Проверить установленный Tick и ready"},
                    {"tool": "g1r_native_enable", "condition": "Только если hook_installed=false и включение явно разрешено"},
                    {"tool": "g1r_native_status", "condition": "Дождаться ready=true; неизвестный результат не является отказом компиляции"},
                    {"tool": "g1r_find_objects", "condition": "Найти актуальную сессионную ссылку и проверить точный объект"},
                    {"tool": "g1r_object_functions", "condition": "Прочитать отражённую сигнатуру, при необходимости function_query"},
                    {"tool": "g1r_invoke", "condition": "Один явно выбранный вызов подтверждённой сигнатуры"},
                    {"tool": "g1r_development_stop", "condition": "При явном завершении своей development-сессии с ожидаемой идентичностью"},
                ],
                "existing_session_sequence": ["g1r_native_status", "g1r_session_status", "g1r_find_objects", "g1r_object_functions"],
                "edit_tools": ["g1r_read_source_exact", "g1r_read_package_file_exact", "g1r_write_package_file"],
                "argument_examples": {
                    "g1r_create_package": {"relative_path": "Example",
                                           "metadata": {"name": "Example", "title": "Example", "author": "Author", "modVersion": "1.0.0"}},
                    "g1r_create_package_directory": {"relative_path": "Example", "directory_path": "Script/Components"},
                    "g1r_snapshot": {"targets": [{"handle": "use_handle_from_find_objects", "properties": ["UseNameFromObjectProperties"]}]},
                    "g1r_invoke": {"handle": "use_handle_from_find_objects", "function": "use_name_from_object_functions",
                                   "arguments": {"use_reflected_parameter_name": None}},
                    "g1r_list_package_files": {"relative_path": "Example", "limit": 100, "offset": 0},
                },
                "ownership": "one_native_tcp_client_session",
                "reconnect": "discard_old_handles_and_watches_then_find_objects_again",
                "uncertain_effect": "never_automatically_retry_outcome_unknown_may_have_executed_do_not_retry",
                "package_format": "GothicMod.json/Script/ScriptPatches",
                "script_patch_engine_apply_confirmed": False,
            },
            "invoke_arguments": {
                "transform": {"type": "FTransform", "struct_path": "/Script/CoreUObject.Transform",
                              "modes": ["input", "const_reference_input"], "size": 96,
                              "reference_input_supported": True, "const_reference_required": True,
                              "input_shape": {"Rotation": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number", "W": "finite_number"},
                                              "Translation": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number"},
                                              "Scale3D": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number"}},
                              "argument_constraints": {"rotation_normalization": "required", "max_norm_squared_deviation": 1e-6, "normalization": "none"},
                              "layout_source": "reflected_function_parameter", "requires_signature_validation": True},
                "scalar_outputs": {"types": ["BoolProperty", "IntProperty", "Int64Property", "UInt32Property",
                                               "UInt64Property", "FloatProperty", "DoubleProperty"],
                                   "argument_marker": {"out": True}, "output_field": "out_parameters",
                                   "metadata_field": "out_parameter_metadata", "pure_output_only": True,
                                   "layout_source": "reflected_function_parameter", "requires_signature_validation": True,
                                   "reference_supported": False, "inout_supported": False},
                "vector": {"type": "FVector", "struct_path": "/Script/CoreUObject.Vector",
                           "modes": ["input", "const_reference_input", "output", "return"],
                           "input_shape": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number"},
                           "output_argument": {"out": True}, "output_field": "out_parameters",
                           "layout_source": "reflected_function_parameter", "requires_signature_validation": True,
                           "reference_input_supported": True, "const_reference_required": True, "inout_supported": False},
            },
            "hot_reload": {"mechanism_confirmed": True, "method": "file_watch_development_mode",
                           "scope": "function_bodies", "runtime_result_requires_verification": True},
            "observations": {"implemented": True, "availability": "requires_live_native_status",
                             "source": "selected_reflected_properties_on_existing_tick",
                             "ownership": "native_tcp_session", "world_generation_confirmed": False},
            "snapshot_comparison": {"implemented": True, "effects": "read_only",
                                    "tool": "g1r_compare_snapshots", "validation": "client_supplied_copies"},
            "limitations": [
                "Исходники не покрывают все нативные bindings и не подтверждают вызовы в текущей игре",
                "Объявления типов найдены лексически; это не AST компилятора и не runtime-рефлексия",
                "Проверка пакета не загружает мод и не проверяет алгоритм сравнения версий загрузчика",
            ],
        }

    @server.tool(annotations=read_only)
    def g1r_find_source_files(query: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Найти страницу относительных .as путей; продолжение через next_offset без атомарного снимка."""
        try:
            return sources.search_files(query, limit, offset)
        except SourceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_search_source(query: str, limit: int = 50, case_sensitive: bool = False, offset: int = 0) -> dict[str, Any]:
        """Искать буквальный текст в официальных .as; вернуть ограниченные строки со ссылками."""
        try:
            return sources.search_text(query, limit, case_sensitive=case_sensitive, offset=offset)
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
    def g1r_list_source_types(query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Найти лексические объявления class/struct/enum; не считать их компиляцией или reflection."""
        try:
            return sources.list_types(query, limit, offset)
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
        """Создать GothicMod.json/Script: metadata требует name, title, author, modVersion; не загружает мод."""
        try:
            return workspace.create_package(relative_path, metadata)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=True, openWorldHint=False))
    def g1r_create_package_directory(relative_path: str, directory_path: str) -> dict[str, Any]:
        """Создать разрешённые каталоги Script внутри пакета; существующий каталог сохраняется."""
        try:
            return workspace.create_package_directory(relative_path, directory_path)
        except WorkspaceError as error:
            raise _tool_failure(error) from None

    @server.tool(annotations=read_only)
    def g1r_list_package_files(relative_path: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Получить страницу дескриптора, .as и ScriptPatches/*.patch; продолжение через next_offset."""
        try:
            return workspace.list_package_files(relative_path, limit, offset)
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
            with native_lock:
                client = native
            return client.request(operation, params)
        except NativeError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error),
                                       "may_have_executed": error.may_have_executed,
                                       "request_id": error.request_id, "effect": error.details}, ensure_ascii=False)) from None

    def development_request(operation: str, *, ctx: Context | None = None, session_reason: str = "Проверка рабочего пакета", **params: Any) -> dict[str, Any]:
        nonlocal native
        try:
            if transport == "streamable-http" and operation in ("start", "stop", "load_native"):
                owner = request_owner(ctx)
                with ownership.lock:
                    acquired_here = False
                    if operation == "start":
                        if params.get("restart"):
                            ownership.require(owner, params.get("expected_process"))
                        acquired_here = not ownership.status()["claimed"]
                        ownership.acquire(owner, session_reason, package=params.get("relative_package"))
                    else:
                        ownership.require(owner, params.get("expected_process"))
                    try:
                        result = getattr(development_service(), operation)(**params)
                    except DevelopmentError as error:
                        if acquired_here and not error.outcome_unknown:
                            ownership.release(owner)
                        raise
                    if operation == "load_native":
                        ownership.bind(owner, result["process"], "native_loaded")
                    elif operation == "start" and result.get("state") == "launch_requested" and result.get("outcome_unknown") is False:
                        ownership.launched(owner, params["relative_package"])
                    elif operation == "stop" and result.get("outcome_unknown") is False and result.get("tool_result", {}).get("state") == "stopped":
                        released = ownership.release(owner)
                        result["session_released"] = released["released"]
            else:
                result = getattr(development_service(), operation)(**params)
            stopped = result.get("tool_result")
            if (operation == "stop" and result.get("state") == "stop_result"
                    and result.get("outcome_unknown") is False and isinstance(stopped, dict)
                    and stopped.get("state") == "stopped"
                    and isinstance(result.get("process"), dict)
                    and stopped.get("process") == result["process"]
                    and isinstance(params.get("expected_process"), dict)
                    and result["process"] == {key: params["expected_process"].get(key)
                                              for key in ("pid", "creation_time", "executable")}):
                with native_lock:
                    previous_native = native
                    native = NativeClient(port=native_port)
                previous_native.close()
            return result
        except DevelopmentError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error),
                                       "outcome_unknown": error.outcome_unknown,
                                       "retry": "only_explicit_new_request"}, ensure_ascii=False)) from None

    @server.tool(annotations=read_only)
    def g1r_session_status() -> dict[str, Any]:
        """Прочитать владельца управления общей игрой; не вызывает игровой Tick."""
        return ownership.status()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def g1r_session_acquire(reason: str, expected_process: dict[str, Any] | None = None, ctx: Context = None) -> dict[str, Any]:
        """Заявить управление общей игрой; другой HTTP-клиент не сможет stop/restart/load. Не запускает игру."""
        try:
            owner = request_owner(ctx)
            with ownership.lock:
                process = None
                if expected_process is not None:
                    service = development_service()
                    process = service._expected_process(expected_process, service.process_state(), allow_bootstrap=True)
                return ownership.acquire(owner, reason, process=process)
        except DevelopmentError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error), "outcome_unknown": error.outcome_unknown}, ensure_ascii=False)) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def g1r_session_release(expected_claim_id: str | None = None, ctx: Context = None) -> dict[str, Any]:
        """Освободить свою заявку без остановки; чужую — только по claim_id при отсутствии игровых процессов."""
        try:
            if expected_claim_id is not None:
                with ownership.lock:
                    return ownership.release_abandoned(expected_claim_id, game_running=development_service().process_state()["running"])
            return ownership.release(request_owner(ctx))
        except DevelopmentError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error), "outcome_unknown": error.outcome_unknown}, ensure_ascii=False)) from None

    @server.tool(annotations=read_only)
    def g1r_compile_diagnostics(relative_package: str, expected_manifest_sha256: str, report_path: str,
                                enable_ingame_tools: StrictBool = False) -> dict[str, Any]:
        """Прочитать UTF-8 .txt/.log из workspace: это предоставленный отчёт, не автоматический compiler API."""
        try:
            return read_report(development_service(), relative_package, expected_manifest_sha256, report_path, enable_ingame_tools)
        except DevelopmentError as error:
            raise ToolError(json.dumps({"code": error.code, "message": str(error), "outcome_unknown": False}, ensure_ascii=False)) from None

    @server.tool(annotations=read_only)
    def g1r_development_recipe(relative_package: str, enable_ingame_tools: StrictBool = False) -> dict[str, Any]:
        """Проверить полный манифест рабочего пакета, процессы и SHA-256 DLL без запуска игры."""
        return development_request("recipe", relative_package=relative_package,
                                   enable_ingame_tools=enable_ingame_tools)

    @server.tool(annotations=read_only)
    def g1r_development_processes() -> dict[str, Any]:
        """Прочитать точные PID, времена создания и пути процессов выбранной игры."""
        return development_request("process_state")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_development_start(relative_package: str, expected_manifest_sha256: str,
                               restart: bool = False,
                             expected_process: dict[str, Any] | None = None,
                             enable_ingame_tools: StrictBool = False, session_reason: str = "Проверка рабочего пакета",
                             ctx: Context = None) -> dict[str, Any]:
        """Запросить development-запуск проверенного пакета; restart требует точную идентичность процесса."""
        return development_request("start", relative_package=relative_package,
                                   expected_manifest_sha256=expected_manifest_sha256,
                                   restart=restart, expected_process=expected_process,
                                   enable_ingame_tools=enable_ingame_tools, ctx=ctx, session_reason=session_reason)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_development_stop(expected_process: dict[str, Any], reason: str = "Проверки завершены", ctx: Context = None) -> dict[str, Any]:
        """Закрыть только подтверждённый экземпляр игры через удерживаемые HANDLE."""
        if not 1 <= len(reason) <= 256 or any(ord(c) < 32 for c in reason):
            raise ToolError(json.dumps({"code":"invalid_stop_reason","message":"Нужна причина остановки (1–256 символов)","outcome_unknown":False}, ensure_ascii=False))
        result = development_request("stop", expected_process=expected_process, ctx=ctx)
        result["stop_reason"] = reason
        return result

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=True))
    def g1r_native_load(expected_process: dict[str, Any], expected_dll_sha256: str, ctx: Context = None) -> dict[str, Any]:
        """Загрузить DLL в проверенный процесс с проверкой хеша; неопределённый результат не повторяется."""
        return development_request("load_native", expected_process=expected_process,
                                   expected_dll_sha256=expected_dll_sha256, ctx=ctx)

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
    def g1r_object_properties(handle: str, limit: int = 50,
                              property_names: list[StrictStr] | None = None) -> dict[str, Any]:
        """Читать свойства ссылки; property_names выбирает до 32 точных уникальных имён в заданном порядке."""
        params = {"handle": handle, "limit": limit}
        if property_names is not None:
            params["property_names"] = property_names
        return native_request("properties", params)

    @server.tool(annotations=read_only)
    def g1r_read_array(handle: str, property_name: str, offset: int = 0, limit: int = 64) -> dict[str, Any]:
        """Скопировать страницу массива; между страницами нет атомарного снимка."""
        return native_request("read_array", {"handle": handle, "property_name": property_name, "offset": offset, "limit": limit})

    @server.tool(annotations=read_only)
    def g1r_read_struct(handle: str, property_name: str) -> dict[str, Any]:
        """Скопировать XYZ или Pitch/Yaw/Roll по фактической reflection без игровых записей."""
        return native_request("read_struct", {"handle": handle, "property_name": property_name})

    @server.tool(annotations=read_only)
    def g1r_object_functions(handle: str, limit: int = 50, function_query: str = "") -> dict[str, Any]:
        """Получить отражённые UFunction объекта и размеры их параметров."""
        params = {"handle": handle, "limit": limit}
        if function_query:
            params["function_query"] = function_query
        return native_request("functions", params)

    @server.tool(annotations=read_only)
    def g1r_snapshot(targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Скопировать до 16 targets={handle, properties:[имена]} на одном Tick; это последовательные чтения."""
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
    def g1r_invoke(handle: str, function: str, arguments: dict[str, Any] | None = None,
                    expected_signature_token: StrictStr | None = None) -> dict[str, Any]:
        """Вызвать UFunction; необязательный token связывает вызов с прочитанной сигнатурой. Неизвестный эффект не повторять."""
        params = {"handle": handle, "function": function, "arguments": arguments or {}}
        if expected_signature_token is not None:
            params["expected_signature_token"] = expected_signature_token
        return native_request("invoke", params)

    return server


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage: ", "Вызов: ", 1).replace("options:", "Параметры:")

        def error(self, message):
            self.exit(2, "Некорректные параметры G1R MCP; используйте --help\n")

    parser = RussianArgumentParser(description="G1R MCP: локальный сервер разработки модов", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--modkit-root", type=Path, required=True, help="Корень официального Modkit")
    parser.add_argument("--workspace-root", type=Path, required=True, help="Корень разрабатываемых пакетов")
    parser.add_argument("--source-revision", help="Полный Git SHA Modkit; при отсутствии ревизия неизвестна")
    parser.add_argument("--native-port", type=int, default=27123, help="Порт localhost экспериментальной DLL")
    parser.add_argument("--game-root", type=Path, help="Корень выбранной игры для команд development-запуска")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio",
                        help="Транспорт MCP; по умолчанию stdio")
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1",
                        help="Адрес HTTP-сервера; разрешён только 127.0.0.1")
    parser.add_argument("--port", type=int, default=27124, help="Порт Streamable HTTP; по умолчанию 27124")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Порт HTTP должен находиться в диапазоне 1024–65535")
    try:
        server = create_server(args.modkit_root, args.workspace_root, args.source_revision, args.native_port, args.game_root,
                               transport=args.transport)
    except (ValueError, OSError, SourceError, NativeError) as error:
        print(f"Не удалось создать G1R MCP: {error}", file=sys.stderr)
        return 2
    if args.transport == "streamable-http":
        server.run(transport="streamable-http", host=args.host, port=args.port,
                   streamable_http_path="/mcp", stateless_http=False, json_response=True,
                   transport_security=TransportSecuritySettings(
                       enable_dns_rebinding_protection=True,
                       allowed_hosts=[f"{args.host}:{args.port}"],
                       allowed_origins=[f"http://{args.host}:{args.port}"]))
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
