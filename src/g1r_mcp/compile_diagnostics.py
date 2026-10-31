"""Ограниченное чтение предоставленного диагностического отчёта; не compiler API."""
from __future__ import annotations
import hashlib
import re

from .development import DevelopmentError
from .workspace import WorkspaceError, _read_bytes, _parts, _checked

MAX_REPORT_BYTES = 1024 * 1024
MAX_DIAGNOSTICS = 200
_ANGEL = re.compile(r"^(?P<file>.+?\.as)\s*\((?P<line>\d+)\s*[:,]\s*(?P<column>\d+)\)\s*:\s*(?P<rest>.+)$", re.I)
_COLON = re.compile(r"^(?P<file>.+?\.as):(?P<line>\d+)(?::(?P<column>\d+))?\s*:\s*(?P<rest>.+)$", re.I)


def parse_report(text: str, package_name: str, files: list[dict]) -> dict:
    known = {item["path"]: item["sha256"] for item in files if item["path"].lower().endswith(".as")}
    diagnostics = []
    unparsed = []
    matched = 0
    for raw in text.splitlines():
        match = _ANGEL.fullmatch(raw.strip()) or _COLON.fullmatch(raw.strip())
        if not match:
            if re.search(r"\b(error|warning|fatal)\b|ошибк", raw, re.I):
                if len(unparsed) < 20: unparsed.append(raw[:2048])
            continue
        matched += 1
        if len(diagnostics) >= MAX_DIAGNOSTICS: continue
        location = match["file"].replace("\\", "/")
        # Абсолютный путь не публикуется. Связь определяется полным суффиксом, не basename.
        options = [path for path in known if location == path
                   or location.endswith("/" + package_name + "/" + path)]
        bound = options[0] if len(options) == 1 else None
        rest = match["rest"]
        severity = "error" if re.match(r"(?:error|fatal)\b", rest, re.I) else "warning" if re.match(r"warning\b", rest, re.I) else "unknown"
        message = re.sub(r"^(?:error|fatal|warning)\s*:\s*", "", rest, flags=re.I)
        diagnostics.append({"file": bound, "line": int(match["line"]),
                            "column": int(match["column"]) if match["column"] else None,
                            "severity": severity, "message": message[:4096],
                            "source_sha256": known.get(bound), "package_source_matched": bound is not None,
                            "stage": "compiler_report"})
    return {"diagnostics": diagnostics, "truncated": matched > MAX_DIAGNOSTICS,
            "unparsed_error_lines": unparsed, "compiled": False,
            "compilation_state": "errors_reported" if any(d["severity"] == "error" for d in diagnostics) else "unknown",
            "compiler_api_available": False, "runtime_validation": "not_performed",
            "report_provenance": "caller_selected_workspace_file",
            "report_matches_runtime_compile": False}


def read_report(service, relative_package: str, expected_manifest_sha256: str,
                report_path: str, enable_ingame_tools: bool = False) -> dict:
    before = service._manifest(relative_package, enable_ingame_tools=enable_ingame_tools)
    if not isinstance(expected_manifest_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_manifest_sha256):
        raise DevelopmentError("invalid_manifest_hash", "Нужен SHA-256 полного пакета development recipe")
    if before["manifest_sha256"] != expected_manifest_sha256.lower():
        raise DevelopmentError("package_changed", "Пакет изменился после подготовки рецепта")
    try:
        target = service.workspace._unprotected(_checked(service.workspace.root.joinpath(*_parts(report_path)), service.workspace.root))
        if target.suffix.lower() not in (".txt", ".log"):
            raise DevelopmentError("invalid_report_path", "Нужен .txt или .log внутри рабочей базы")
        raw = _read_bytes(target, service.workspace.root, MAX_REPORT_BYTES)
        text = raw.decode("utf-8-sig", errors="strict")
    except WorkspaceError as error:
        raise DevelopmentError(error.code, str(error)) from None
    except UnicodeError:
        raise DevelopmentError("invalid_report_encoding", "Диагностический отчёт должен быть UTF-8") from None
    after = service._manifest(relative_package, enable_ingame_tools=enable_ingame_tools)
    if after["manifest_sha256"] != before["manifest_sha256"]:
        raise DevelopmentError("package_changed", "Пакет изменился во время чтения диагностики")
    return {"package_name": before["package_name"], "manifest_sha256": before["manifest_sha256"],
            "report_sha256": hashlib.sha256(raw).hexdigest(), **parse_report(text, before["package_name"], before["files"])}
