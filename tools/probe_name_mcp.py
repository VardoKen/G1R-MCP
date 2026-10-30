"""Проверить возврат FName на собственном объекте через готовый MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
from probe_cleanup import settle_task, deferred_sigint
from probe_object_mcp import ProbeError, _caller, _single_error

HOST = "G1RMCPNameProbe"
PROPERTIES = {"StoredEntry": "Entry", "StoredNone": "None"}
METHODS = {"ReadMarker": ("IntProperty", 4), "ReadEntry": ("NameProperty", 8), "ReadNone": ("NameProperty", 8),
           "ReadText": ("StrProperty", 16), "ReadEmptyText": ("StrProperty", 16)}


def check_target(item: dict) -> dict:
    if (not isinstance(item, dict) or item.get("name") != "Default__" + HOST
            or item.get("class") != HOST or item.get("path") != "/Script/Angelscript.Default__" + HOST
            or not isinstance(item.get("handle"), str) or not item["handle"].startswith("obj-")):
        raise ProbeError("unsafe_probe_target", "Не подтверждён собственный FName-проверочный CDO")
    return item


def check_signature(functions: dict, method_name: str) -> dict:
    if method_name not in METHODS or not isinstance(functions, dict):
        raise ProbeError("probe_signature_invalid", "Неизвестный метод FName-fixture")
    matches = [item for item in functions.get("items", []) if item.get("name") == method_name]
    if len(matches) != 1:
        raise ProbeError("probe_signature_absent", "Метод FName-fixture не найден однозначно")
    method = matches[0]
    parameters = method.get("parameters")
    if not isinstance(parameters, list) or len(parameters) != 1 or not isinstance(parameters[0], dict):
        raise ProbeError("probe_signature_mismatch", "Метод должен иметь только возвращаемое значение")
    value = parameters[0]
    expected_type, expected_size = METHODS[method_name]
    size, offset = method.get("parameter_size"), value.get("offset")
    if (value.get("return") is not True or value.get("reference") is True
            or value.get("type") != expected_type or value.get("element_size") != expected_size
            or type(size) is not int or not 0 <= size <= 32 or type(offset) is not int
            or offset < 0 or max(size, offset + expected_size) > 32):
        raise ProbeError("probe_signature_mismatch", "Не подтверждён нулевой вход и тип возврата проверочного метода")
    return method


def check_snapshot(snapshot: dict, target: dict, session_id: str) -> None:
    objects = snapshot.get("objects")
    if (snapshot.get("session_id") != session_id or snapshot.get("partial") is not False
            or snapshot.get("truncated") is not False or not isinstance(objects, list) or len(objects) != 1
            or snapshot.get("source") != "live_native" or snapshot.get("consistency") != "single_tick_sequential"
            or any(objects[0].get(key) != target[key] for key in ("handle", "path", "class")) or objects[0].get("error")):
        raise ProbeError("probe_snapshot_invalid", "Не подтверждён полный FName-снимок текущей сессии")
    for name, expected in PROPERTIES.items():
        matches = [field for field in objects[0].get("properties", []) if field.get("name") == name]
        if (len(matches) != 1 or matches[0].get("type") != "NameProperty" or matches[0].get("supported") is not True
                or matches[0].get("error") or type(matches[0].get("value")) is not str or matches[0]["value"] != expected):
            raise ProbeError("probe_name_property", "Не совпало ожидаемое значение собственного FName-свойства")


async def exercise(call) -> dict:
    report = {"checks": [], "cleanup": [], "game_launched": False, "dll_loaded": False}
    watch_id = None
    failure = None
    try:
        status = await call("g1r_native_status", {})
        session_id = status.get("session_id")
        if not status.get("ready") or not isinstance(session_id, str) or not session_id:
            raise ProbeError("game_not_ready", "Не подтверждены готовая очередь и текущая сессия")
        found = await call("g1r_find_objects", {"query": "Default__" + HOST, "class_query": HOST, "limit": 50})
        exact = [item for item in found.get("items", []) if item.get("name") == "Default__" + HOST]
        if len(exact) != 1:
            raise ProbeError("probe_object_absent", "Собственный FName-проверочный CDO не найден однозначно")
        target = check_target(exact[0])
        functions = await call("g1r_object_functions", {"handle": target["handle"], "limit": 50})
        for method_name in METHODS:
            check_signature(functions, method_name)

        async def invoke(method):
            result = await call("g1r_invoke", {"handle": target["handle"], "function": method, "arguments": {}})
            if result.get("executed") is not True:
                raise ProbeError("probe_not_executed", "Не подтверждено выполнение собственного метода")
            if METHODS[method][0] == "StrProperty" and result.get("cleanup_confirmed") is not True:
                raise ProbeError("probe_string_cleanup_unconfirmed", "Возврат FString не подтвердил освобождение памяти")
            return result.get("result")

        marker = await invoke("ReadMarker")
        if type(marker) is not int or marker != 71:
            raise ProbeError("probe_marker_mismatch", "Собственный CDO не вернул маркер 71")
        report["checks"].append("exact_cdo_marker_and_zero_input_signatures")
        results = {}
        for method, expected in (("ReadEntry", "Entry"), ("ReadNone", "None"), ("ReadText", "G1R MCP dev7"), ("ReadEmptyText", "")):
            values = [await invoke(method) for _ in range(3)]
            if any(type(value) is not str or value != expected for value in values):
                raise ProbeError("probe_name_result", "Не совпал или изменился повторный возврат FName/FString")
            results[method] = {"text": expected, "repetitions": len(values)}
        report["returns"] = results
        snapshot = await call("g1r_snapshot", {"targets": [{"handle": target["handle"], "properties": list(PROPERTIES)}]})
        check_snapshot(snapshot, target, session_id)
        report["checks"].append("repeated_entry_none_returns_match_properties")
        report["checks"].append("repeated_nonempty_empty_fstring_returns_cleanup_confirmed")
        watch = await call("g1r_watch_start", {"handle": target["handle"], "properties": list(PROPERTIES),
                                                "duration_ms": 5000, "interval_ms": 50, "capacity": 8})
        watch_id = watch["watch_id"]
        await asyncio.sleep(0.25)
        observed = await call("g1r_watch_read", {"watch_id": watch_id, "after_seq": 0, "limit": 100})
        events = observed.get("events", [])
        if (observed.get("session_id") != session_id or observed.get("state") != "running"
                or observed.get("dropped_events") != 0 or observed.get("gap") is True
                or len(events) != 1 or events[0].get("kind") != "baseline"):
            raise ProbeError("probe_false_name_change", "Неизменные FName-свойства породили потери или ложные изменения")
        check_snapshot(events[0]["snapshot"], target, session_id)
        report["checks"].append("short_stable_watch_one_baseline_no_emitted_changes")
        report["watch"] = {"events": len(events), "dropped_events": observed["dropped_events"]}
    except BaseException as error:
        failure = error
        raise
    finally:
        async def finish_cleanup():
            errors = []
            if watch_id is not None:
                for method in ("g1r_watch_stop", "g1r_watch_forget"):
                    try:
                        result = await call(method, {"watch_id": watch_id})
                        if (result.get("cleanup_complete") is not True
                                or method == "g1r_watch_forget" and result.get("forgotten") is not True
                                or method == "g1r_watch_stop" and result.get("state") not in {"stopped", "completed", "failed"}):
                            raise ProbeError("probe_cleanup_unconfirmed", "Остановка наблюдения не подтверждена")
                        report["cleanup"].append(method)
                    except BaseException as error:
                        errors.append(f"{method}: {getattr(error, 'code', 'failed')}")
            if errors:
                message = "Не подтверждена очистка: " + "; ".join(errors)
                if failure is None:
                    raise ProbeError("probe_cleanup_failed", message)
                failure.add_note(message)
        try:
            await settle_task(asyncio.create_task(finish_cleanup()), timeout=30, label='name cleanup')
        except BaseException as cleanup_error:
            if failure is None:
                raise
            failure.add_note("Не подтверждено завершение очистки: " + str(cleanup_error))
    return report


async def run_probe(args) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "g1r_mcp.server", "--modkit-root", str(args.modkit_root),
        "--workspace-root", str(args.workspace_root), "--native-port", str(args.native_port)], cwd=str(REPOSITORY_ROOT))
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as client:
            initialized = await client.initialize()
            report = await exercise(_caller(client))
            report["server_version"] = initialized.server_info.version
            return report


async def _run_with_signals(args):
    with deferred_sigint():
        return await run_probe(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить возвраты FName на собственной fixture через готовый MCP")
    parser.add_argument("--modkit-root", type=Path, default=REPOSITORY_ROOT.parent / "G1R-Modkit")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--native-port", type=int, default=27123)
    args = parser.parse_args()
    try:
        report = asyncio.run(_run_with_signals(args))
    except Exception as error:
        cause = _single_error(error)
        print(json.dumps({"code": getattr(cause, "code", "probe_failed"), "message": str(cause),
                          "notes": getattr(cause, "__notes__", [])}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
