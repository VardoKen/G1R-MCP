"""Проверить собственную object-fixture через настоящий MCP; игру и DLL не запускать."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROBE_CLASSES = {
    "G1RMCPObjectProbe": 41,
    "G1RMCPDerivedObjectProbe": 41,
    "G1RMCPOtherObjectProbe": 99,
}
PROPERTIES = ["StoredObject", "StoredTypedObject"]
SIGNATURES = {
    "ReadMarker": ([], ("IntProperty", 4)),
    "ReadSelf": ([], ("ObjectProperty", 8)),
    "EchoObject": ([("Input", "ObjectProperty", 8)], ("ObjectProperty", 8)),
    "EchoTypedObject": ([("Input", "ObjectProperty", 8)], ("ObjectProperty", 8)),
    "IsNullObject": ([("Input", "ObjectProperty", 8)], ("BoolProperty", 1)),
    "StoreObject": ([("Input", "ObjectProperty", 8)], None),
    "StoreTypedObject": ([("Input", "ObjectProperty", 8)], None),
}


class ProbeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_target(item: dict, class_name: str) -> dict:
    """Ограничить любые записи точными собственными CDO этого проверочного пакета."""
    if (class_name not in PROBE_CLASSES or not isinstance(item, dict)
            or item.get("name") != f"Default__{class_name}"
            or item.get("class") != class_name
            or item.get("path") != f"/Script/Angelscript.Default__{class_name}"
            or not isinstance(item.get("handle"), str) or not item["handle"].startswith("obj-")):
        raise ProbeError("unsafe_probe_target", "Не подтверждён точный собственный CDO object-fixture")
    return item


def check_signature(functions: dict, method_name: str) -> dict:
    if method_name not in SIGNATURES or not isinstance(functions, dict):
        raise ProbeError("probe_signature_invalid", "Неизвестный проверочный метод")
    matches = [item for item in functions.get("items", []) if item.get("name") == method_name]
    if len(matches) != 1:
        raise ProbeError("probe_signature_absent", "Проверочный метод не найден однозначно")
    method = matches[0]
    params = method.get("parameters")
    inputs, output = SIGNATURES[method_name]
    if not isinstance(params, list) or len(params) != len(inputs) + (output is not None):
        raise ProbeError("probe_signature_mismatch", "Не совпало число параметров проверочного метода")
    if any(not isinstance(parameter, dict) or parameter.get("reference") is True
           or parameter.get("out") is True and parameter.get("return") is not True for parameter in params):
        raise ProbeError("probe_signature_mismatch", "Не поддержан out/ref проверочного метода")
    actual_inputs = [parameter for parameter in params if parameter.get("return") is not True]
    actual_outputs = [parameter for parameter in params if parameter.get("return") is True]
    if len(actual_inputs) != len(inputs) or len(actual_outputs) != (output is not None):
        raise ProbeError("probe_signature_mismatch", "Не совпали входы и возврат проверочного метода")
    for parameter, expected in zip(actual_inputs, inputs):
        if (parameter.get("name"), parameter.get("type"), parameter.get("element_size")) != expected:
            raise ProbeError("probe_signature_mismatch", "Не совпал тип входа проверочного метода")
    if output is not None and (actual_outputs[0].get("type"), actual_outputs[0].get("element_size")) != output:
        raise ProbeError("probe_signature_mismatch", "Не совпал тип возврата проверочного метода")
    size = method.get("parameter_size")
    if type(size) is not int or not 0 <= size <= 32:
        raise ProbeError("probe_signature_mismatch", "Не подтверждён ограниченный буфер проверочного метода")
    for parameter in params:
        offset, element_size = parameter.get("offset"), parameter.get("element_size")
        if (type(offset) is not int or type(element_size) is not int or offset < 0 or element_size < 1
                or offset + element_size > 32
                or parameter.get("return") is not True and offset + element_size > size):
            raise ProbeError("probe_signature_mismatch", "Не совпала раскладка буфера проверочного метода")
    # AngelScript's reflected ParmsSize may cover inputs while ReturnValue extends it.
    effective_span = max([size] + [parameter["offset"] + parameter["element_size"] for parameter in params])
    if effective_span > 32:
        raise ProbeError("probe_signature_mismatch", "Превышен ограниченный полный буфер проверочного метода")
    return method


def check_marker(result: dict, class_name: str) -> None:
    if result.get("executed") is not True or type(result.get("result")) is not int or result["result"] != PROBE_CLASSES[class_name]:
        raise ProbeError("probe_marker_mismatch", "Не подтверждён маркер собственного CDO")


def check_object_result(result: dict, expected: dict | None) -> None:
    if result.get("executed") is not True:
        raise ProbeError("probe_not_executed", "Проверочный метод не подтвердил выполнение")
    value = result.get("result")
    if expected is None:
        if value is not None:
            raise ProbeError("probe_null_mismatch", "Null-аргумент не вернулся как null")
    elif (not isinstance(value, dict) or any(value.get(key) != expected.get(key)
          for key in ("handle", "index", "name", "path", "class"))
          or not isinstance(value.get("session_id"), str) or not value["session_id"]):
        raise ProbeError("probe_identity_mismatch", "Не сохранена объектная идентичность")


def snapshot_values(snapshot: dict, target: dict) -> dict:
    objects = snapshot.get("objects")
    if (snapshot.get("partial") is not False or snapshot.get("truncated") is not False
            or not isinstance(objects, list) or len(objects) != 1
            or objects[0].get("handle") != target["handle"] or objects[0].get("error")):
        raise ProbeError("probe_snapshot_invalid", "Не подтверждён полный снимок проверочных свойств")
    fields = objects[0].get("properties", [])
    values = {}
    for name in PROPERTIES:
        matches = [field for field in fields if field.get("name") == name]
        if (len(matches) != 1 or matches[0].get("supported") is not True
                or matches[0].get("type") != "ObjectProperty" or matches[0].get("error")):
            raise ProbeError("probe_snapshot_invalid", "ObjectProperty не прочитан однозначно")
        values[name] = matches[0].get("value")
    return values


def error_payload(result) -> dict:
    for item in result.content:
        if item.type == "text":
            try:
                # FastMCP may prepend a method label before the structured error.
                value = json.loads(item.text[item.text.index("{"):])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(value, dict):
                return value
    return {}


async def exercise(call) -> dict:
    """Один владелец MCP; при любом отказе попытаться восстановить только собственные поля."""
    targets, signature_sets = {}, {}
    report = {"checks": [], "cleanup": [], "game_launched": False, "dll_loaded": False}
    watch_id = None
    writable_target = None
    active_error = None
    try:
        status = await call("g1r_native_status", {})
        if not status.get("ready"):
            raise ProbeError("game_not_ready", "Нативная игровая очередь не готова")
        for class_name in PROBE_CLASSES:
            found = await call("g1r_find_objects", {"query": f"Default__{class_name}", "class_query": class_name, "limit": 50})
            exact = [item for item in found.get("items", []) if item.get("name") == f"Default__{class_name}"]
            if len(exact) != 1:
                raise ProbeError("probe_object_absent", "Точный собственный проверочный CDO не найден")
            target = check_target(exact[0], class_name)
            targets[class_name] = target
            functions = await call("g1r_object_functions", {"handle": target["handle"], "limit": 100})
            methods = ["ReadMarker"] if class_name == "G1RMCPOtherObjectProbe" else list(SIGNATURES)
            signature_sets[class_name] = {method: check_signature(functions, method) for method in methods}
            marker = await call("g1r_invoke", {"handle": target["handle"], "function": "ReadMarker", "arguments": {}})
            check_marker(marker, class_name)
        report["checks"].append("three_exact_cdos_signatures_markers")
        base, derived, other = (targets[name] for name in PROBE_CLASSES)
        writable_target = base

        async def invoke(method, value=...):
            arguments = {} if value is ... else {"Input": value}
            return await call("g1r_invoke", {"handle": base["handle"], "function": method, "arguments": arguments})

        # Baseline normalization is confined to the exact fixture CDO whose signatures were checked.
        await invoke("StoreObject", None)
        await invoke("StoreTypedObject", None)
        before = await call("g1r_snapshot", {"targets": [{"handle": base["handle"], "properties": PROPERTIES}]})
        if any(value is not None for value in snapshot_values(before, base).values()):
            raise ProbeError("probe_initial_state", "Проверочные поля не вернулись к null")
        self_result = await invoke("ReadSelf")
        check_object_result(self_result, base)
        for method in ("EchoObject", "EchoTypedObject"):
            check_object_result(await invoke(method, {"handle": base["handle"]}), base)
            check_object_result(await invoke(method, None), None)
        check_object_result(await invoke("EchoTypedObject", {"handle": derived["handle"]}), derived)
        for value, expected in ((None, True), ({"handle": base["handle"]}, False)):
            result = await invoke("IsNullObject", value)
            if result.get("executed") is not True or result.get("result") is not expected:
                raise ProbeError("probe_null_predicate", "Проверка null вернула неверный результат")
        mismatch = await call("g1r_invoke", {"handle": base["handle"], "function": "EchoTypedObject",
                                              "arguments": {"Input": {"handle": other["handle"]}}},
                              expected_error="incompatible_object_argument")
        report["class_mismatch"] = mismatch
        report["checks"].append("object_returns_echo_null_derived_class_mismatch")

        watch = await call("g1r_watch_start", {"handle": base["handle"], "properties": PROPERTIES,
                                                "duration_ms": 5000, "interval_ms": 50, "capacity": 16})
        watch_id = watch["watch_id"]
        await invoke("StoreObject", {"handle": other["handle"]})
        await invoke("StoreTypedObject", {"handle": derived["handle"]})
        after = await call("g1r_snapshot", {"targets": [{"handle": base["handle"], "properties": PROPERTIES}]})
        values = snapshot_values(after, base)
        for name, target in (("StoredObject", other), ("StoredTypedObject", derived)):
            check_object_result({"executed": True, "result": values[name]}, target)
        deadline = time.monotonic() + 2
        observed = None
        while time.monotonic() < deadline:
            events = await call("g1r_watch_read", {"watch_id": watch_id, "after_seq": 0, "limit": 100})
            if events.get("dropped_events") != 0 or events.get("gap") is True:
                raise ProbeError("probe_watch_loss", "Короткая проверка потеряла события")
            changed = [event for event in events.get("events", []) if event.get("kind") == "changed"]
            if changed and snapshot_values(changed[-1]["snapshot"], base) == values:
                observed = events
                break
            await asyncio.sleep(0.05)
        if observed is None:
            raise ProbeError("probe_watch_change_absent", "Наблюдение не подтвердило новые объектные поля")
        await asyncio.sleep(0.2)
        stable = await call("g1r_watch_read", {"watch_id": watch_id, "after_seq": 0, "limit": 100})
        if stable.get("last_seq") != observed.get("last_seq"):
            raise ProbeError("probe_false_object_change", "Неизменный объект породил ложные watch-события")
        diff = await call("g1r_compare_snapshots", {"before": before, "after": after, "limit": 100})
        if diff.get("value_changes") != 2:
            raise ProbeError("probe_snapshot_diff", "Сравнение не обнаружило два объектных изменения")
        report["checks"].append("object_properties_snapshots_watch_change_and_stability")
        report["watch"] = {"changed_events": sum(event.get("kind") == "changed" for event in stable["events"]),
                           "dropped_events": stable["dropped_events"], "last_seq": stable["last_seq"]}
        report["previous_handle"] = base["handle"]
        report["previous_watch_id"] = watch_id
    except BaseException as error:
        active_error = error
        raise
    finally:
        failures = []
        if writable_target is not None:
            for method in ("StoreObject", "StoreTypedObject"):
                try:
                    await call("g1r_invoke", {"handle": writable_target["handle"], "function": method, "arguments": {"Input": None}})
                    report["cleanup"].append(method)
                except Exception as error:
                    failures.append(f"{method}: {getattr(error, 'code', 'failed')}")
            try:
                restored = await call("g1r_snapshot", {"targets": [{"handle": writable_target["handle"], "properties": PROPERTIES}]})
                if any(value is not None for value in snapshot_values(restored, writable_target).values()):
                    raise ProbeError("probe_restore_mismatch", "Проверочные поля после очистки не равны null")
                report["cleanup"].append("restored_null_snapshot")
            except Exception as error:
                failures.append(f"restore_snapshot: {getattr(error, 'code', 'failed')}")
        if watch_id is not None:
            for name in ("g1r_watch_stop", "g1r_watch_forget"):
                try:
                    await call(name, {"watch_id": watch_id})
                    report["cleanup"].append(name)
                except Exception as error:
                    failures.append(f"{name}: {getattr(error, 'code', 'failed')}")
        if failures:
            if active_error is None:
                raise ProbeError("probe_cleanup_failed", "; ".join(failures))
            if hasattr(active_error, "add_note"):
                active_error.add_note("Не подтверждена очистка: " + "; ".join(failures))
    return report


def _caller(client):
    async def call(name, arguments, *, expected_error=None):
        result = await client.call_tool(name, arguments)
        if expected_error is not None:
            error = error_payload(result)
            if (not result.is_error or error.get("code") != expected_error
                    or error.get("may_have_executed") is True):
                raise ProbeError("probe_error_mismatch", "Не подтверждён ожидаемый отказ до выполнения")
            return {"code": error["code"], "may_have_executed": error.get("may_have_executed", False)}
        if result.is_error:
            error = error_payload(result)
            raise ProbeError(error.get("code", "mcp_call_failed"), error.get("message", "Вызов MCP завершился ошибкой"))
        if not isinstance(result.structured_content, dict):
            raise ProbeError("mcp_result_invalid", "Не получен объект структурированного результата")
        return result.structured_content
    return call


async def run_probe(args: argparse.Namespace) -> dict:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "g1r_mcp.server",
        "--modkit-root", str(args.modkit_root), "--workspace-root", str(args.workspace_root),
        "--native-port", str(args.native_port)], cwd=str(REPOSITORY_ROOT))
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as client:
            initialized = await client.initialize()
            report = await exercise(_caller(client))
            report["server_version"] = initialized.server_info.version
    async with stdio_client(parameters) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as client:
            await client.initialize()
            call = _caller(client)
            await call("g1r_native_status", {})
            stale = await call("g1r_invoke", {"handle": report["previous_handle"], "function": "ReadMarker", "arguments": {}},
                               expected_error="stale_handle")
            report["new_session_rejection"] = stale
    report.pop("previous_handle", None)
    report.pop("previous_watch_id", None)
    report["checks"].append("old_session_handle_rejected_before_execution")
    return report


def _single_error(error: BaseException) -> BaseException:
    """Сохранить конкретную причину одиночного отказа внутри AnyIO task group."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить только собственные CDO object-fixture через готовый MCP")
    parser.add_argument("--modkit-root", type=Path, default=REPOSITORY_ROOT.parent / "G1R-Modkit")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--native-port", type=int, default=27123)
    args = parser.parse_args()
    try:
        report = asyncio.run(run_probe(args))
    except Exception as error:
        cause = _single_error(error)
        print(json.dumps({"code": getattr(cause, "code", "probe_failed"), "message": str(cause),
                          "error_type": type(cause).__name__, "notes": getattr(cause, "__notes__", [])}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
