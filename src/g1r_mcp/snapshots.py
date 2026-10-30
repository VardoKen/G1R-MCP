"""Сравнение ограниченных копий снимков без обращения к игровой среде.

Переданные клиентом поля происхождения являются заявлениями клиента.
Равенство выбранных значений не доказывает неизменность игрового мира.
"""
from __future__ import annotations

import json
from typing import Any

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_DIFF_BYTES = 1024 * 1024
MAX_OBJECTS = 16
MAX_PROPERTIES = 32
MAX_ITEMS = 200
MAX_OBJECT_INDEX = 4_000_000
ENUM_INTEGER_RANGES = {
    "IntProperty": (-(2**31), 2**31 - 1),
    "Int64Property": (-(2**63), 2**63 - 1),
    "UInt32Property": (0, 2**32 - 1),
    "UInt64Property": (0, 2**64 - 1),
    "ByteProperty": (0, 255),
}


class SnapshotError(Exception):
    """Некорректная или слишком большая копия снимка."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _invalid(message: str) -> None:
    raise SnapshotError("invalid_snapshot", message)


def _text(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 8192:
        _invalid(f"{label}: требуется непустая строка до 8192 символов")


def _integer(value: Any, label: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        _invalid(f"{label}: требуется {'положительное' if positive else 'неотрицательное'} целое число")


def _error(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"code", "message"}:
        _invalid("Ошибка объекта или свойства должна содержать code и message")
    _text(value["code"], "Код ошибки")
    if not isinstance(value["message"], str) or len(value["message"]) > 8192:
        _invalid("Сообщение ошибки должно быть строкой до 8192 символов")


def _object_value(value: Any, session_id: str) -> None:
    """Проверить копию непрозрачной ссылки, не разрешая её в игровой среде."""
    if value is None:
        return
    expected = {"handle", "index", "name", "path", "class", "session_id"}
    if not isinstance(value, dict) or set(value) != expected:
        _invalid("ObjectProperty должен содержать null или точный дескриптор объекта")
    _text(value["handle"], "Ссылка значения ObjectProperty")
    if len(value["handle"].encode("utf-8")) > 80:
        _invalid("Ссылка значения ObjectProperty превышает предел 80 байт")
    _integer(value["index"], "Индекс значения ObjectProperty")
    if value["index"] >= MAX_OBJECT_INDEX:
        _invalid("Индекс значения ObjectProperty превышает предел реестра")
    for key, bound in (("name", 4096), ("class", 4096), ("path", 8192)):
        _text(value[key], f"ObjectProperty.{key}")
        if len(value[key].encode("utf-8")) > bound:
            _invalid(f"ObjectProperty.{key}: строка превышает предел {bound} байт")
    _text(value["session_id"], "Сессия значения ObjectProperty")
    if value["session_id"] != session_id:
        _invalid("Дескриптор ObjectProperty принадлежит другой сессии снимка")


def _value_kind(prop: dict[str, Any]) -> str:
    return "object_reference" if prop["type"] == "ObjectProperty" else type(prop["value"]).__name__


def _enum_metadata(metadata: Any, prop: dict[str, Any]) -> None:
    expected = {"path", "underlying_type", "name_status", "value_name", "matched_count", "ambiguous", "reason"}
    if not isinstance(metadata, dict) or set(metadata) != expected:
        _invalid("Неверный набор метаданных enum")
    if prop.get("type") not in {"EnumProperty", "ByteProperty"}:
        _invalid("Метаданные enum допускаются только для EnumProperty или ByteProperty")
    underlying = metadata["underlying_type"]
    if not isinstance(underlying, str) or underlying not in ENUM_INTEGER_RANGES:
        _invalid("Неизвестный числовой тип enum")
    if prop["type"] == "ByteProperty" and underlying != "ByteProperty":
        _invalid("ByteProperty требует соответствующий базовый тип enum")
    if prop["supported"]:
        minimum, maximum = ENUM_INTEGER_RANGES[underlying]
        if type(prop.get("value")) is not int or not minimum <= prop["value"] <= maximum:
            _invalid("Числовое значение enum не соответствует базовому типу")
    for key, bound in (("path", 8192), ("value_name", 4096), ("reason", 128)):
        if metadata[key] is not None:
            _text(metadata[key], f"enum.{key}")
            if len(metadata[key].encode("utf-8")) > bound or "\0" in metadata[key]:
                _invalid(f"enum.{key}: строка превышает предел или содержит NUL")
    count = metadata["matched_count"]
    _integer(count, "Число совпадений enum")
    if count > 4096 or type(metadata["ambiguous"]) is not bool or metadata["ambiguous"] != (count > 1):
        _invalid("Неверные сведения о совпадениях enum")
    status = metadata["name_status"]
    if not isinstance(status, str) or status not in {"matched", "not_found", "unavailable", "value_unrepresentable"}:
        _invalid("Неизвестный статус имени enum")
    if status == "matched":
        if count == 0 or metadata["value_name"] is None or metadata["path"] is None or metadata["reason"] is not None:
            _invalid("matched требует имя, путь и совпадение без ошибки")
    elif metadata["value_name"] is not None:
        _invalid("Непрочитанное имя enum должно быть null")
    if status in {"not_found", "value_unrepresentable"} and count != 0:
        _invalid("Статус enum без совпадений требует matched_count=0")
    if status == "not_found" and metadata["path"] is None:
        _invalid("not_found требует известный путь enum")
    if status == "value_unrepresentable" and (
        underlying != "UInt64Property" or not prop["supported"] or prop["value"] <= 2**63 - 1
    ):
        _invalid("value_unrepresentable требует uint64 за пределом int64")


def _encoded_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid("Снимок должен быть конечным UTF-8 JSON без NaN и Infinity")


def _snapshot(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        _invalid("Снимок должен быть JSON-объектом")
    if _encoded_size(value) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("snapshot_too_large", "Копия снимка превышает 1 МиБ")
    allowed = {"snapshot_id", "source", "session_id", "process_id", "thread_id", "tick_count",
               "consistency", "objects", "truncated", "partial", "limits"}
    required = allowed - {"source", "limits"}
    if not required <= value.keys() or not value.keys() <= allowed:
        _invalid("Неверный набор полей снимка")
    for key in ("snapshot_id", "session_id", "consistency"):
        _text(value[key], key)
    if "source" in value:
        _text(value["source"], "source")
    for key in ("process_id", "thread_id"):
        _integer(value[key], key, positive=True)
    _integer(value["tick_count"], "tick_count")
    for key in ("truncated", "partial"):
        if type(value[key]) is not bool:
            _invalid(f"{key}: требуется логическое значение")
    if "limits" in value:
        limits = value["limits"]
        expected = {"objects", "properties_per_object", "scan_fields_per_object",
                    "property_value_bytes", "snapshot_bytes"}
        if not isinstance(limits, dict) or set(limits) != expected:
            _invalid("Неверный набор заявленных лимитов снимка")
        for key in expected:
            _integer(limits[key], f"limits.{key}", positive=True)
    objects = value["objects"]
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        _invalid("Снимок допускает не более 16 выбранных объектов")
    result: dict[str, dict[str, Any]] = {}
    for obj in objects:
        if not isinstance(obj, dict) or not obj.keys() <= {
            "handle", "path", "class", "properties", "error", "partial", "truncated"
        }:
            _invalid("Неверная форма объекта в снимке")
        _text(obj.get("handle"), "handle")
        if obj["handle"] in result:
            _invalid("Снимок содержит повторяющуюся ссылку объекта")
        if "error" in obj:
            _error(obj["error"])
        else:
            for key in ("path", "class"):
                _text(obj.get(key), key)
        for key in ("path", "class"):
            if key in obj:
                _text(obj[key], key)
        for key in ("partial", "truncated"):
            if key in obj and type(obj[key]) is not bool:
                _invalid(f"Сведения объекта {key}: требуется логическое значение")
        properties = obj.get("properties", [] if "error" in obj else None)
        if not isinstance(properties, list) or len(properties) > MAX_PROPERTIES:
            _invalid("Объект допускает не более 32 выбранных свойств")
        names: set[str] = set()
        for prop in properties:
            if not isinstance(prop, dict) or not prop.keys() <= {"name", "type", "supported", "value", "error", "enum"}:
                _invalid("Неверная форма свойства в снимке")
            _text(prop.get("name"), "Имя свойства")
            if prop["name"] in names:
                _invalid("Объект содержит повторяющееся имя свойства")
            names.add(prop["name"])
            if type(prop.get("supported")) is not bool:
                _invalid("supported: требуется логическое значение")
            if "type" in prop:
                _text(prop["type"], "Тип свойства", optional=not prop["supported"])
            if "error" in prop:
                _error(prop["error"])
            if prop["supported"]:
                if "value" not in prop or "type" not in prop or "error" in prop:
                    _invalid("Поддержанное свойство должно иметь тип и значение без ошибки")
                if prop["type"] == "ObjectProperty":
                    _object_value(prop["value"], value["session_id"])
                elif prop["type"] == "ClassProperty":
                    _invalid("ClassProperty не поддержан контрактом копий снимков")
                elif type(prop["value"]) not in {bool, int, float, str}:
                    _invalid("Поддержанное значение должно быть скаляром")
            elif "value" in prop and prop["value"] is not None:
                _invalid("Неподдержанное свойство не должно выдавать значение за прочитанное")
            if "enum" in prop:
                _enum_metadata(prop["enum"], prop)
            elif prop.get("type") == "EnumProperty" and prop["supported"] and (
                type(prop["value"]) is not int or not -(2**63) <= prop["value"] <= 2**64 - 1
            ):
                _invalid("Значение EnumProperty должно быть целым числом в пределах int64/uint64")
            if prop.get("type") == "NameProperty" and prop["supported"] and not isinstance(prop["value"], str):
                _invalid("Значение NameProperty должно быть строкой")
            if prop.get("type") == "ByteProperty" and prop["supported"] and (
                type(prop["value"]) is not int or not 0 <= prop["value"] <= 255
            ):
                _invalid("Значение ByteProperty должно быть целым числом от 0 до 255")
        result[obj["handle"]] = obj
    return result


def compare_snapshots(before: dict[str, Any], after: dict[str, Any], limit: int = 100) -> dict[str, Any]:
    """Сравнить выбранные скалярные поля копий; не проверяет их игровое происхождение."""
    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
        raise SnapshotError("invalid_limit", "Предел сравнения должен быть от 1 до 200")
    old = _snapshot(before)
    new = _snapshot(after)
    old_incomplete = sum(bool(obj.get("partial") or obj.get("truncated")) for obj in old.values())
    new_incomplete = sum(bool(obj.get("partial") or obj.get("truncated")) for obj in new.values())
    result: dict[str, Any] = {
        "source": "copied_snapshots", "validation": "client_supplied_copies", "provenance": "unverified",
        "before_snapshot_id": before["snapshot_id"], "after_snapshot_id": after["snapshot_id"],
        "comparable": True, "reason": "same_session_process", "items": [], "total_items": 0,
        "metadata_annotations": [], "metadata_annotation_count": 0,
        "value_changes": 0, "type_changes": 0, "unknown_count": 0, "coverage_changes": 0,
        "truncated": False, "equal_selected_values": None,
        "coverage": {"before_partial": before["partial"], "after_partial": after["partial"],
                     "before_truncated": before["truncated"], "after_truncated": after["truncated"],
                     "before_targets": len(old), "after_targets": len(new),
                     "before_incomplete_objects": old_incomplete, "after_incomplete_objects": new_incomplete,
                     "complete": not any((before["partial"], after["partial"], before["truncated"],
                                          after["truncated"], old_incomplete, new_incomplete))},
    }
    for field, reason in (("process_id", "different_process"), ("session_id", "different_session")):
        if before[field] != after[field]:
            result.update(comparable=False, reason=reason)
            result["coverage"]["complete"] = False
            return result

    entries: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []

    def annotate(handle: str, name: str, a: dict[str, Any], b: dict[str, Any]) -> None:
        left_enum, right_enum = a.get("enum"), b.get("enum")
        if left_enum is None and right_enum is None:
            return
        unavailable = any(metadata is None or metadata["name_status"] != "matched"
                          for metadata in (left_enum, right_enum))
        if unavailable or left_enum != right_enum:
            annotations.append({
                "kind": "enum_metadata_unavailable" if unavailable else "enum_metadata_changed",
                "handle": handle, "property": name, "before": left_enum, "after": right_enum,
            })

    def emit(kind: str, **fields: Any) -> None:
        entries.append({"kind": kind, **fields})
        if kind == "value_changed":
            result["value_changes"] += 1
        elif kind == "type_changed":
            result["type_changes"] += 1
        elif kind in {"coverage_added", "coverage_removed"}:
            result["coverage_changes"] += 1
        else:
            result["unknown_count"] += 1

    source_known = before.get("source") == after.get("source") == "live_native"
    if not source_known:
        result["coverage"]["complete"] = False
        emit("unknown", reason="source_not_claimed_live_native")
    if before["consistency"] != "single_tick_sequential" or after["consistency"] != "single_tick_sequential":
        result["coverage"]["complete"] = False
        emit("unknown", reason="unsupported_consistency")
        source_known = False

    for handle in sorted(old.keys() | new.keys()):
        if handle not in old or handle not in new:
            emit("coverage_added" if handle in new else "coverage_removed", handle=handle,
                 reason="selected_target_set_changed")
            continue
        left, right = old[handle], new[handle]
        if "error" in left or "error" in right:
            errors = [obj["error"]["code"] for obj in (left, right) if "error" in obj]
            kind = "object_unavailable" if any(code in {"stale_handle", "object_destroying"} for code in errors) else "unknown"
            emit(kind, handle=handle, reason="object_read_failed", before_error=left.get("error"),
                 after_error=right.get("error"))
            continue
        if any(left[key] != right[key] for key in ("path", "class")):
            emit("unknown", handle=handle, reason="identity_mismatch")
            continue
        left_props = {prop["name"]: prop for prop in left["properties"]}
        right_props = {prop["name"]: prop for prop in right["properties"]}
        for name in sorted(left_props.keys() | right_props.keys()):
            if name not in left_props or name not in right_props:
                emit("unknown", handle=handle, property=name, reason="selected_property_coverage_changed")
                continue
            a, b = left_props[name], right_props[name]
            annotate(handle, name, a, b)
            if not source_known or not a["supported"] or not b["supported"]:
                emit("unknown", handle=handle, property=name,
                     reason="source_not_claimed_live_native" if not source_known else "property_not_readable",
                     before_error=a.get("error"), after_error=b.get("error"))
                continue
            if a["type"] == b["type"] == "ObjectProperty":
                av, bv = a["value"], b["value"]
                if av is not None and bv is not None and av["handle"] == bv["handle"] and av != bv:
                    emit("unknown", handle=handle, property=name, reason="object_reference_identity_mismatch")
                    continue
                if av != bv:
                    emit("value_changed", handle=handle, property=name, before=av, after=bv)
                continue
            if a["type"] != b["type"] or _value_kind(a) != _value_kind(b):
                emit("type_changed", handle=handle, property=name,
                     before_type=a["type"], after_type=b["type"],
                     before_value_kind=_value_kind(a), after_value_kind=_value_kind(b))
            if type(a["value"]) is not type(b["value"]) or a["value"] != b["value"]:
                emit("value_changed", handle=handle, property=name, before=a["value"], after=b["value"])

    result["total_items"] = len(entries)
    result["metadata_annotation_count"] = len(annotations)
    result["coverage"]["complete"] &= not bool(result["unknown_count"] or result["coverage_changes"])
    result["equal_selected_values"] = (
        not bool(result["value_changes"] or result["type_changes"])
        if result["coverage"]["complete"] else None
    )
    for entry in entries[:limit]:
        result["items"].append(entry)
        if _encoded_size(result) > MAX_DIFF_BYTES:
            result["items"].pop()
            break
    remaining = limit - len(result["items"])
    for annotation in annotations[:remaining]:
        result["metadata_annotations"].append(annotation)
        if _encoded_size(result) > MAX_DIFF_BYTES:
            result["metadata_annotations"].pop()
            break
    result["truncated"] = (len(result["items"]) < len(entries)
                           or len(result["metadata_annotations"]) < len(annotations))
    # Все вложенные значения ответа копируются: вызывающий код не получает
    # изменяемые элементы входных снимков даже для записей об ошибках.
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
