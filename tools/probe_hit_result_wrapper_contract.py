"""Own-AS FHitResult wrapper: raw hit storage остаётся внутри AngelScript."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
from probe_cleanup import deferred_sigint, settle_task
from probe_function_contract_mcp import TOKEN, make_caller
from probe_object_mcp import ProbeError, _single_error
from probe_object_array_input_contract import CONSTRAINTS
from probe_scalar_out_contract import UNKNOWN_GAME, check_rejection, check_scalar
from probe_vector_contract import check_parameter_vector_metadata

HOST = "G1RMCPHitResultWrapperProbe"
MARKER = 251
METHODS = ("ReadMarker", "ReadCallsCount", "ResetProbe", "ReadDefaultImpactPoint", "HasWorld", "TraceImpactPoint")
ZERO = {"X": 0, "Y": 0, "Z": 0}


def finite_vector(value):
    if (not isinstance(value, dict) or set(value) != set(ZERO)
            or any(type(component) not in (int, float) or not math.isfinite(component) for component in value.values())):
        raise ProbeError("probe_wrapper_vector", "Wrapper не вернул ровно три конечные FVector компоненты")
    return value


def check_signature(listing, name):
    if (name not in METHODS or not isinstance(listing, dict) or listing.get("truncated") is not False
            or listing.get("scan_complete") is not True or listing.get("scan_truncated") is not False
            or not isinstance(listing.get("items"), list)):
        raise ProbeError("probe_signature_invalid", "Не подтверждён полный каталог собственного HitResult wrapper")
    matches = [item for item in listing["items"] if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1: raise ProbeError("probe_signature_absent", "HitResult wrapper метод не найден однозначно")
    method, fields = matches[0], matches[0].get("parameters")
    size = method.get("parameter_size")
    if (not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields)
            or method.get("parameters_truncated") is not False or type(size) is not int or not 0 <= size <= 128
            or type(method.get("parameter_count")) is not int or method["parameter_count"] != len(fields)):
        raise ProbeError("probe_signature_mismatch", "Не подтверждены NumParms и ограниченный wrapper буфер")
    returned = [field for field in fields if field.get("return") is True]
    args = [field for field in fields if field.get("return") is False]
    expected_return = {"ReadMarker": ("IntProperty", 4), "ReadCallsCount": ("IntProperty", 4),
                       "TraceImpactPoint": ("BoolProperty", 1), "HasWorld": ("BoolProperty", 1)}.get(name)
    if name == "ReadDefaultImpactPoint":
        expected_return = ("StructProperty", returned[0].get("element_size")) if len(returned) == 1 else None
        if expected_return not in (("StructProperty", 12), ("StructProperty", 24)):
            raise ProbeError("probe_signature_mismatch", "DefaultImpactPoint не подтвердил FVector12/24")
    if (len(returned) != (expected_return is not None) or len(args) != (4 if name == "TraceImpactPoint" else 0)
            or len(fields) != len(returned) + len(args)
            or returned and (returned[0].get("type"), returned[0].get("element_size")) != expected_return):
        raise ProbeError("probe_signature_mismatch", "Не совпали wrapper аргументы и exact возврат")
    if args and ({field.get("name") for field in args} != {"Start", "End", "ActorsToIgnore", "ImpactPoint"}):
        raise ProbeError("probe_signature_mismatch", "Не совпали exact имена wrapper аргументов")
    spans, vector_size = [], None
    for field in fields:
        flags, offset, width = field.get("flags"), field.get("offset"), field.get("element_size")
        if (not isinstance(field.get("name"), str) or type(flags) is not int or not 0 <= flags < 2**64 or not flags & 0x80
                or bool(flags & 0x400) is not field["return"] or bool(flags & 0x100) is not field.get("out")
                or bool(flags & 0x2) is not field.get("const") or bool(flags & 0x08000000) is not field.get("reference")
                or type(offset) is not int or offset < 0 or type(width) is not int or width < 1 or offset + width > 128
                or type(field.get("array_dim")) is not int or field["array_dim"] != 1
                or field["return"] is False and offset + width > size):
            raise ProbeError("probe_signature_flags", "Wrapper CPF/ArrayDim/границы не подтверждены")
        if any(offset < end and start < offset + width for start, end in spans):
            raise ProbeError("probe_signature_mismatch", "Wrapper параметры пересекаются")
        spans.append((offset, offset + width))
        if field["return"]:
            if flags & (0x2 | 0x08000000): raise ProbeError("probe_signature_flags", "Wrapper возвращает по значению")
        if field.get("type") == "StructProperty":
            if width not in (12, 24) or vector_size is not None and width != vector_size:
                raise ProbeError("probe_signature_mismatch", "Wrapper FVector widths не совпали")
            vector_size = width
            direction = "return" if field["return"] else "output" if field["name"] == "ImpactPoint" else "input"
            check_parameter_vector_metadata(field, width, direction)
            if not field["return"]:
                if field["name"] in ("Start", "End"):
                    if flags & (0x100 | 0x2 | 0x08000000) or field.get("passing") != "value":
                        raise ProbeError("probe_signature_flags", "Start/End должны быть FVector по значению")
                elif field["name"] == "ImpactPoint":
                    if not flags & 0x100 or flags & (0x2 | 0x08000000):
                        raise ProbeError("probe_signature_flags", "ImpactPoint требует чистый out без const/ref")
                else: raise ProbeError("probe_signature_mismatch", "Лишний FVector wrapper аргумент")
        elif field["return"] is False:
            metadata = field.get("array")
            if (field["name"] != "ActorsToIgnore" or field.get("type") != "ArrayProperty" or width != 16
                    or not flags & 0x2 or not flags & (0x100 | 0x08000000) or field.get("direction") != "input"
                    or field.get("passing") != "const_reference" or field.get("argument_constraints") != CONSTRAINTS
                    or not isinstance(metadata, dict) or metadata.get("inner_type") != "ObjectProperty"
                    or type(metadata.get("inner_element_size")) is not int or metadata["inner_element_size"] != 8
                    or type(metadata.get("inner_array_dim")) is not int or metadata["inner_array_dim"] != 1
                    or type(metadata.get("inner_offset")) is not int or metadata["inner_offset"] != 0
                    or not isinstance(metadata.get("property_class"), dict)
                    or metadata["property_class"].get("path") != "/Script/Engine.Actor" or metadata["property_class"].get("name") != "Actor"):
                raise ProbeError("probe_wrapper_array", "ActorsToIgnore не подтвердил const ObjectProperty8 array объявленного Actor")
    token = method.get("signature_token")
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ProbeError("probe_signature_token_absent", "Wrapper метод не получил signature token")
    return method, token, vector_size


async def exercise(call):
    cleanup_required, failure = False, None
    report = {"checks": [], "rejections": [], "cleanup": [], "reflected_functions": {}, "game_launched": False,
              "dll_loaded": False, "raw_hit_result": "not_marshaled", "actor_spawn": "not_tested", "world_context": "unverified"}
    try:
        status = await call("g1r_native_status", {})
        session_id = status.get("session_id")
        if status.get("ready") is not True or not isinstance(session_id, str) or not session_id:
            raise ProbeError("game_not_ready", "Не подтверждены Tick и native-сессия")
        found = await call("g1r_find_objects", {"query": HOST, "class_query": HOST, "limit": 100})
        if found.get("truncated") is not False or not isinstance(found.get("items"), list):
            raise ProbeError("probe_object_absent", "Каталог собственных wrapper экземпляров неполон")
        cdos, instances = [], []
        for item in found["items"]:
            if (not isinstance(item, dict) or item.get("class") != HOST or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("path"), str) or not isinstance(item.get("handle"), str)
                    or not item["handle"].startswith("obj-")):
                raise ProbeError("unsafe_probe_target", "Не подтверждён exact собственный wrapper объект")
            if item["name"] == "Default__" + HOST and item["path"] == "/Script/Angelscript.Default__" + HOST:
                cdos.append(item)
            elif (item["name"].startswith("Default__") or "Default__" in item["path"] or item["path"].startswith("/Script/")
                    or not item["name"].startswith(HOST) or not item["path"].endswith(item["name"])):
                raise ProbeError("unsafe_probe_target", "CDO/шаблон нельзя считать живым экземпляром wrapper")
            else:
                instances.append(item)
        if len(cdos) != 1 or len(instances) != 1:
            raise ProbeError("probe_object_absent", "Требуются один exact CDO и один живой UScriptWorldSubsystem wrapper экземпляр")
        target = instances[0]
        report["cdo"] = cdos[0]
        report["live_instance"] = target
        report["reflected_cdo_functions"] = {}
        for name in METHODS:
            listing = await call("g1r_object_functions", {"handle": cdos[0]["handle"], "function_query": name, "limit": 10})
            report["reflected_cdo_functions"][name] = listing
            check_signature(listing, name)
        handle, tokens, widths = target["handle"], {}, {}
        for name in METHODS:
            listing = await call("g1r_object_functions", {"handle": handle, "function_query": name, "limit": 10})
            report["reflected_functions"][name] = listing
            _, tokens[name], widths[name] = check_signature(listing, name)
        if len(set(tokens.values())) != len(tokens) or widths["ReadDefaultImpactPoint"] != widths["TraceImpactPoint"]:
            raise ProbeError("probe_signature_mismatch", "Wrapper tokens алиасны или FVector widths не совпали")

        async def invoke(name, arguments=None, *, expected_error=None):
            supplied = {} if arguments is None else arguments
            json.dumps(supplied, allow_nan=False)
            return await call("g1r_invoke", {"handle": handle, "function": name, "arguments": supplied,
                              "expected_signature_token": tokens[name]}, expected_error=expected_error)

        check_scalar(await invoke("ReadMarker"), "IntProperty", MARKER)
        check_scalar(await invoke("ReadCallsCount"), "IntProperty", 0)
        cleanup_required = True
        default = await invoke("ReadDefaultImpactPoint")
        if default.get("executed") is not True or default.get("return_type") != "StructProperty":
            raise ProbeError("probe_wrapper_default", "ReadDefaultImpactPoint не подтвердил FVector")
        finite_vector(default.get("result"))
        check_parameter_vector_metadata({"struct": default.get("result_metadata"), "direction": "return"}, widths["ReadDefaultImpactPoint"], "return")
        count = 1
        check_scalar(await invoke("ReadCallsCount"), "IntProperty", count)
        report.update(session_id=session_id, handle=handle, signature_tokens=tokens, default_impact_point=default["result"])
        report["checks"].append("own_marker251_counter0_local_as_hitresult_constructor_finite_impact_no_trace")
        world = await invoke("HasWorld")
        if world.get("executed") is not True or world.get("return_type") != "BoolProperty" or type(world.get("result")) is not bool:
            raise ProbeError("probe_wrapper_world", "HasWorld не подтвердил Bool доступности world context")
        check_scalar(await invoke("ReadCallsCount"), "IntProperty", count)
        report["has_world"] = world["result"]
        if world["result"] is False:
            report["world_context"] = "world_context_unavailable"
            report["trace"] = {"tested": False, "reason": "world_context_unavailable"}
            report["checks"].append("trace_skipped_without_world_context")
            report["completed_mutating_calls"] = count
            return report
        report["world_context"] = "available_confirmed_by_own_HasWorld"
        arguments = {"Start": ZERO, "End": ZERO, "ActorsToIgnore": [], "ImpactPoint": {"out": True}}
        trace = await invoke("TraceImpactPoint", arguments)
        if trace.get("cleanup_confirmed") is not True or trace.get("array_input_cleanup_confirmed") is not True:
            raise ProbeError("probe_wrapper_cleanup_unconfirmed", "Trace wrapper не подтвердил очистку ActorsToIgnore")
        if trace.get("executed") is not True or trace.get("return_type") != "BoolProperty" or type(trace.get("result")) is not bool:
            raise ProbeError("probe_wrapper_trace", "Trace wrapper не подтвердил Bool результат")
        output, metadata = trace.get("out_parameters"), trace.get("out_parameter_metadata")
        if not isinstance(output, dict) or set(output) != {"ImpactPoint"} or not isinstance(metadata, dict) or set(metadata) != {"ImpactPoint"}:
            raise ProbeError("probe_wrapper_trace", "Trace wrapper не вернул ровно один FVector ImpactPoint")
        finite_vector(output["ImpactPoint"])
        check_parameter_vector_metadata({"struct": metadata["ImpactPoint"], "direction": "output", "argument_marker": {"out": True}}, widths["TraceImpactPoint"], "output")
        if trace["result"] is False and any(component != 0 for component in output["ImpactPoint"].values()):
            raise ProbeError("probe_wrapper_trace", "False TraceImpactPoint не сохранил явную ZeroVector ветку собственной AS-fixture")
        count += 1
        check_scalar(await invoke("ReadCallsCount"), "IntProperty", count)
        report["trace"] = {"tested": True, "arguments": arguments, "hit": trace["result"], "impact_point": output["ImpactPoint"],
            "cleanup_confirmed": trace["cleanup_confirmed"], "array_input_cleanup_confirmed": trace["array_input_cleanup_confirmed"],
            "world_context": "trace_executed_without_assuming_loaded_world"}
        report["checks"].append("one_degenerate_trace_drawNone_noActorSpawn_bool_and_finite_vector_out")
        cases = [("Start", None, "invalid_argument"), ("End", {"X": True, "Y": 0, "Z": 0}, "invalid_argument"),
                 ("ActorsToIgnore", None, "invalid_argument"), ("ActorsToIgnore", [True], "invalid_object_argument"),
                 ("ActorsToIgnore", [None] * 33, "invalid_argument"), ("ImpactPoint", {"out": False}, "invalid_argument"),
                 ("ImpactPoint", {"out": 1}, "invalid_argument")]
        for key, value, code in cases:
            supplied = {**arguments, key: value}
            refusal = await invoke("TraceImpactPoint", supplied, expected_error=code)
            report["last_refusal"] = refusal
            effect = refusal.get("effect")
            for source in (refusal, effect if isinstance(effect, dict) else {}):
                if (source.get("cleanup_confirmed") is False or source.get("array_input_cleanup_confirmed") is False
                        or source.get("cleanup_state") == "unknown" or source.get("object_array_input_quarantined") is True):
                    raise ProbeError("probe_wrapper_cleanup_unconfirmed", "Отказ wrapper не подтвердил cleanup; дальнейшие вызовы прекращены")
            check_rejection(refusal, "TraceImpactPoint", supplied, code)
            check_scalar(await invoke("ReadCallsCount"), "IntProperty", count)
            report["rejections"].append({"arguments": supplied, "code": code})
        report["completed_mutating_calls"] = count
        return report
    except BaseException as error:
        failure = error
        report["failure_signal"] = {"code": getattr(error, "code", "probe_failed"), "message": str(error)}
        error.add_note("HitResult wrapper report: " + json.dumps(report, ensure_ascii=False))
        raise
    finally:
        if cleanup_required:
            if getattr(failure, "code", None) in UNKNOWN_GAME or getattr(failure, "code", None) in {
                    "game_result_error", "probe_wrapper_cleanup_unconfirmed", "object_array_input_quarantined",
                    "probe_contract_rejection_unconfirmed", "probe_scalar_out_rejection_unconfirmed"}:
                failure.add_note("Очистка HitResult wrapper пропущена после неопределённого исполнения/владения; игру завершает владелец development-сеанса")
            else:
                async def finish_cleanup():
                    check_scalar(await invoke("ResetProbe"), "", None)
                    check_scalar(await invoke("ReadCallsCount"), "IntProperty", 0)
                    check_scalar(await invoke("ReadMarker"), "IntProperty", MARKER)
                    report["cleanup"].append("ResetProbe_zero_counter_marker251")
                try: await settle_task(asyncio.create_task(finish_cleanup()), timeout=30, label="hit-result wrapper cleanup")
                except BaseException as error:
                    if failure is None: raise
                    failure.add_note("Не подтверждена очистка HitResult wrapper CDO: " + str(error))


async def run(args):
    if args.mcp_url not in {"http://127.0.0.1:27124/mcp", "http://localhost:27124/mcp"}:
        raise ProbeError("invalid_mcp_url", "Wrapper проба использует общий локальный HTTP MCP")
    async with streamable_http_client(args.mcp_url) as connection:
        async with ClientSession(connection[0], connection[1]) as session:
            initialized = await session.initialize()
            return {"server_version": initialized.server_info.version, **await exercise(make_caller(session))}


def main():
    parser = argparse.ArgumentParser(description="Проверить AS HitResult wrapper своего живого WorldSubsystem через общий HTTP MCP")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:27124/mcp")
    args = parser.parse_args()
    async def protected():
        with deferred_sigint(): return await run(args)
    try: print(json.dumps(asyncio.run(protected()), indent=2, ensure_ascii=False)); return 0
    except BaseException as error:
        error = _single_error(error)
        print(json.dumps({"code": getattr(error, "code", "probe_failed"), "message": str(error), "notes": getattr(error, "__notes__", [])}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8"); sys.exit(main())
