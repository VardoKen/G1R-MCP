"""Проверить полный development-цикл через MCP и закрыть созданную игру.

Требует заранее подготовленный официальный compile-probe пакет. Отказывается
работать при уже запущенной игре. Изменяющие запросы выполняются ровно один раз;
после получения точной идентичности своего процесса остановка обязательна.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ntpath
from pathlib import Path
import re
import sys
import time
from typing import Any, Awaitable, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from probe_live_mcp import ProbeError, check_signature

ROOT = Path(__file__).resolve().parents[1]
Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z\Z")


def checked_identity(value: object) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"pid", "creation_time", "executable"}
            or type(value["pid"]) is not int or not 1 <= value["pid"] <= 0xFFFFFFFF
            or not isinstance(value["creation_time"], str) or not _TIME.fullmatch(value["creation_time"])
            or not isinstance(value["executable"], str) or not value["executable"]):
        raise ProbeError("invalid_process_identity", "Процесс не имеет точной подтверждённой идентичности")
    return dict(value)


def select_process(state: object, game_root: str, *, bootstrap: bool = False) -> dict[str, Any] | None:
    if not isinstance(state, dict) or not isinstance(state.get("processes"), list):
        raise ProbeError("invalid_process_state", "Неверный ответ списка игровых процессов")
    expected = ntpath.normcase(ntpath.join(game_root, *(('G1R-Win64-Shipping.exe',) if bootstrap else
                              ('G1R', 'Binaries', 'Win64', 'G1R-Win64-Shipping.exe'))))
    identities = [checked_identity(item) for item in state["processes"]]
    if len(identities) > 2:
        raise ProbeError("ambiguous_process", "Слишком много игровых процессов")
    inner = [item for item in identities if ntpath.normcase(item["executable"]) == expected]
    if len(inner) > 1:
        raise ProbeError("ambiguous_process", "Внутренний игровой процесс найден неоднозначно")
    return inner[0] if inner else None


def select_inner(state: object, game_root: str) -> dict[str, Any] | None:
    return select_process(state, game_root)


async def development_cycle(call: Call, relative_package: str, expected_value: int, *,
                            startup_timeout: float = 45, ready_timeout: float = 45,
                            sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                            clock: Callable[[], float] = time.monotonic,
                            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not 1 <= startup_timeout <= 45 or not 1 <= ready_timeout <= 60:
        raise ProbeError("invalid_timeout", "Ожидание запуска ограничено 45 с, Tick — 60 с")
    report: dict[str, Any] = {"source": "real_stdio_development_cycle", "stages": []}

    def stage(name: str) -> None:
        report["stages"].append(name)
        if progress is not None:
            progress(name)

    recipe = await call("g1r_development_recipe", {"relative_package": relative_package})
    manifest = recipe.get("manifest")
    artifact = recipe.get("native_artifact")
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("game_root"), str)
            or not isinstance(manifest.get("manifest_sha256"), str)
            or not _SHA.fullmatch(manifest["manifest_sha256"])
            or not isinstance(artifact, dict) or artifact.get("available") is not True
            or not isinstance(artifact.get("sha256"), str) or not _SHA.fullmatch(artifact["sha256"])):
        raise ProbeError("recipe_incomplete", "Recipe не подтвердил полный пакет и доступную DLL")
    initial = await call("g1r_development_processes", {})
    if not isinstance(initial.get("processes"), list) or initial["processes"]:
        raise ProbeError("game_already_running", "Проверка требует закрытую игру; существующий процесс не трогается")
    stage("recipe_checked")
    owned: dict[str, Any] | None = None
    owned_bootstrap: dict[str, Any] | None = None
    primary: BaseException | None = None
    try:
        launch = await call("g1r_development_start", {
            "relative_package": relative_package,
            "expected_manifest_sha256": manifest["manifest_sha256"], "restart": False})
        if launch.get("state") != "launch_requested" or launch.get("outcome_unknown") is not False:
            raise ProbeError("launch_unconfirmed", "Запрос запуска не подтверждён; автоматически не повторяется")
        stage("launch_requested")
        deadline = clock() + startup_timeout
        while clock() < deadline:
            state = await call("g1r_development_processes", {})
            candidate_bootstrap = select_process(state, manifest["game_root"], bootstrap=True)
            if owned_bootstrap is None and candidate_bootstrap is not None:
                owned_bootstrap = candidate_bootstrap
                report["owned_bootstrap"] = dict(owned_bootstrap)
            elif candidate_bootstrap is not None and candidate_bootstrap != owned_bootstrap:
                raise ProbeError("bootstrap_process_changed", "Идентичность загрузочного процесса изменилась")
            owned = select_inner(state, manifest["game_root"])
            if owned is not None:
                break
            await sleep(.5)
        if owned is None:
            raise ProbeError("game_start_timeout", "Внутренний процесс игры не появился в срок")
        report["owned_process"] = dict(owned)
        stage("process_identified")
        # Повторная проверка recipe связывает загрузку с текущими пакетом и DLL.
        current = await call("g1r_development_recipe", {"relative_package": relative_package})
        if current.get("manifest", {}).get("manifest_sha256") != manifest["manifest_sha256"]:
            raise ProbeError("package_changed", "Пакет изменился во время запуска")
        current_artifact = current.get("native_artifact", {})
        if current_artifact.get("available") is not True or current_artifact.get("sha256") != artifact["sha256"]:
            raise ProbeError("dll_changed", "DLL изменилась во время запуска")
        loaded = await call("g1r_native_load", {"expected_process": owned,
                          "expected_dll_sha256": artifact["sha256"]})
        if (loaded.get("state") != "load_result" or loaded.get("process") != owned
                or loaded.get("dll_sha256") != artifact["sha256"] or loaded.get("outcome_unknown") is not False):
            raise ProbeError("load_unconfirmed", "Ответ не подтвердил однократную загрузку выбранной DLL")
        stage("dll_loaded")
        # LoadLibrary завершается раньше init-потока DLL; повторяем только чтение статуса.
        deadline = clock() + min(10, ready_timeout)
        while True:
            try:
                status = await call("g1r_native_status", {})
                break
            except ProbeError as error:
                if error.code not in {"native_unavailable", "native_timeout", "native_connection_failed"} or clock() >= deadline:
                    raise
                await sleep(.1)
        if status.get("process_id") != owned["pid"]:
            raise ProbeError("native_process_mismatch", "Мост принадлежит другому процессу")
        observations = [{key: status.get(key) for key in
                         ("process_id", "hook_installed", "ready", "tick_count", "game_thread_id", "startup_error")}]
        if not status.get("hook_installed"):
            enabled = await call("g1r_native_enable", {})
            if enabled.get("process_id") != owned["pid"]:
                raise ProbeError("native_process_mismatch", "Результат включения относится к другому процессу")
            status = enabled
            observations.append({key: status.get(key) for key in
                                 ("process_id", "hook_installed", "ready", "tick_count", "game_thread_id", "startup_error")})
        deadline = clock() + ready_timeout
        while not status.get("ready") and clock() < deadline:
            await sleep(.1)
            status = await call("g1r_native_status", {})
            if status.get("process_id") != owned["pid"]:
                raise ProbeError("native_process_mismatch", "Процесс нативного канала изменился")
            selected = {key: status.get(key) for key in
                        ("process_id", "hook_installed", "ready", "tick_count", "game_thread_id", "startup_error")}
            if selected != observations[-1] and len(observations) < 12:
                observations.append(selected)
        if status.get("ready") is not True:
            raise ProbeError("game_not_ready", "Нативная очередь Tick не начала работу в срок: "
                             + json.dumps({"wait_seconds": ready_timeout, "observations": observations,
                                           "last_status": status}, ensure_ascii=False))
        report["readiness_observations"] = observations
        stage("tick_ready")
        deadline = clock() + ready_timeout
        while True:
            found = await call("g1r_find_objects", {"query": "Default__G1RMCPCompileProbe",
                               "class_query": "G1RMCPCompileProbe", "limit": 20})
            matches = [item for item in found.get("items", [])
                       if item.get("name") == "Default__G1RMCPCompileProbe"
                       and item.get("class") == "G1RMCPCompileProbe"
                       and item.get("path") == "/Script/Angelscript.Default__G1RMCPCompileProbe"]
            if matches or clock() >= deadline:
                break
            await sleep(.2)
        if len(matches) != 1:
            raise ProbeError("probe_object_absent", "Точный CDO проверочного пакета не найден однозначно")
        target = matches[0]
        signature = check_signature(await call("g1r_object_functions", {"handle": target["handle"], "limit": 100}))
        stage("signature_verified")
        result = await call("g1r_invoke", {"handle": target["handle"], "function": "ReadProbeValue", "arguments": {}})
        if result.get("executed") is not True or type(result.get("result")) is not int or result["result"] != expected_value:
            raise ProbeError("probe_value_mismatch", "Проверочный метод не вернул ожидаемую константу")
        report.update({"compiled_probe": True, "probe_value": result["result"],
                       "native_version": status.get("version"), "probe_signature": signature})
        stage("probe_verified")
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_process = owned if owned is not None else owned_bootstrap
        if cleanup_process is not None:
            try:
                stopped = await call("g1r_development_stop", {"expected_process": cleanup_process})
                if (stopped.get("state") != "stop_result" or stopped.get("process") != cleanup_process
                        or stopped.get("tool_result", {}).get("state") != "stopped"):
                    raise ProbeError("stop_unconfirmed", "Остановка собственного процесса не подтверждена")
                state = await call("g1r_development_processes", {})
                if state.get("processes") != []:
                    raise ProbeError("game_lingering", "После остановки остались игровые процессы")
                report["stop_confirmed"] = True
                stage("game_stopped")
            except BaseException as cleanup:
                if primary is None:
                    raise
                raise ProbeError("cleanup_failed", f"Проверка завершилась ошибкой {type(primary).__name__}; остановка: {cleanup}") from primary
    return report


async def run(args: argparse.Namespace) -> dict[str, Any]:
    parameters = StdioServerParameters(command=sys.executable,
        args=["-B", "-m", "g1r_mcp.server", "--modkit-root", str(args.modkit_root),
              "--workspace-root", str(args.workspace_root), "--native-port", str(args.native_port)]
             + (["--game-root", str(args.game_root)] if args.game_root else []),
        cwd=str(ROOT), env={"PYTHONUTF8": "1"})
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as session:
            initialized = await session.initialize()
            tools = (await session.list_tools()).tools
            required = {"g1r_development_recipe", "g1r_development_processes", "g1r_development_start",
                        "g1r_development_stop", "g1r_native_load", "g1r_native_status", "g1r_native_enable"}
            if not required <= {tool.name for tool in tools}:
                raise ProbeError("development_tools_absent", "MCP не предоставляет полный development-цикл")

            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = await session.call_tool(name, arguments)
                if result.is_error:
                    text = next((item.text for item in result.content if item.type == "text"), "Ошибка MCP")
                    try:
                        structured_error = json.loads(text)
                    except (ValueError, TypeError):
                        structured_error = {}
                    code = structured_error.get("code", "mcp_call_failed") if isinstance(structured_error, dict) else "mcp_call_failed"
                    raise ProbeError(code, f"{name}: {text}")
                if not isinstance(result.structured_content, dict):
                    raise ProbeError("mcp_result_invalid", f"{name}: отсутствует структурированный объект")
                return result.structured_content

            def progress(stage: str) -> None:
                print(f"MCP development: {stage}", file=sys.stderr, flush=True)

            report = await development_cycle(call, args.relative_package, args.expected_value,
                    startup_timeout=args.startup_timeout, ready_timeout=args.ready_timeout, progress=progress)
            return {"server_version": initialized.server_info.version, "tool_count": len(tools), **report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить запуск, DLL и остановку игры через настоящий MCP")
    parser.add_argument("--modkit-root", type=Path, default=ROOT.parent / "G1R-Modkit")
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--game-root", type=Path)
    parser.add_argument("--native-port", type=int, default=27123)
    parser.add_argument("--relative-package", default="artifacts/compile-probe-dev3/G1RMCPProbe")
    parser.add_argument("--expected-value", type=int, choices=(1, 2), default=2)
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--ready-timeout", type=float, default=45)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
            error = error.exceptions[0]
        print(json.dumps({"error": getattr(error, "code", "probe_failed"), "message": str(error),
                          "error_type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
