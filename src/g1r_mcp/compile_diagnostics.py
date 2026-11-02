"""Read a supplied diagnostic report in full; this is not a compiler API."""
from __future__ import annotations
import hashlib
import re

from .development import DevelopmentError
from .workspace import WorkspaceError, _read_bytes, _parts, _checked

_FILE = r'''(?P<file>"[^"\r\n]+\.as"|'[^'\r\n]+\.as'|[^\r\n]+?\.as)'''
_ANGEL = re.compile(r"^" + _FILE + r"\s*\((?P<line>\d+)\s*[:,]\s*(?P<column>\d+)\)\s*:\s*(?P<rest>.+)$", re.I)
_COLON = re.compile(r"^" + _FILE + r":(?P<line>\d+)(?::(?P<column>\d+))?\s*:\s*(?P<rest>.+)$", re.I)
_HEADER = re.compile(r"^<RichTextBlock\.Bold>(?P<file>.+)</>:$")
_STYLE = re.compile(r"^\s*<(?P<style>Log\.Error|Log\.Warning|NormalText\.Subdued)>(?P<message>.*)$")
_POSITION = re.compile(r"^\((?P<line>\d+):(?P<column>\d+)\) (?P<message>[\s\S]*)$")


def parse_report(text: str, package_name: str, files: list[dict]) -> dict:
    known = {item["path"]: item["sha256"] for item in files if item["path"].lower().endswith(".as")}
    diagnostics = []
    unparsed = []
    current_file = None

    def append_diagnostic(location, line, column, severity, message):
        if location and location[0] in ('"', "'") and location[-1] == location[0]:
            location = location[1:-1]
        location = location.replace("\\", "/") if location else None
        # Do not expose absolute paths. Match the full suffix rather than the basename.
        options = [path for path in known if location == path
                   or (location is not None and location.endswith("/" + package_name + "/" + path))]
        bound = options[0] if len(options) == 1 else None
        diagnostics.append({"file": bound, "line": line, "column": column,
                            "severity": severity, "message": message, "message_truncated": False,
                            "source_sha256": known.get(bound), "package_source_matched": bound is not None,
                            "stage": "compiler_report"})

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        header = _HEADER.fullmatch(raw.strip())
        if header:
            current_file = header["file"]
            continue
        style = _STYLE.fullmatch(raw)
        if style:
            message = style["message"]
            while not message.endswith("</>") and index < len(lines):
                message += "\n" + lines[index]
                index += 1
            if not message.endswith("</>"):
                unparsed.append(raw)
                continue
            message = message[:-3]
            position = _POSITION.fullmatch(message)
            line = column = None
            if position:
                try:
                    line, column = int(position["line"]), int(position["column"])
                except ValueError:
                    unparsed.append(raw)
                    continue
                message = position["message"]
            severity = {"Log.Error": "error", "Log.Warning": "warning", "NormalText.Subdued": "info"}[style["style"]]
            append_diagnostic(current_file, line, column, severity, message)
            continue
        if current_file is not None:
            message = raw.lstrip()
            position = _POSITION.fullmatch(message)
            if position and position["message"].startswith("Compiling "):
                append_diagnostic(current_file, int(position["line"]), int(position["column"]),
                                  "info", position["message"])
                continue
            if message.startswith("Compiling "):
                append_diagnostic(current_file, None, None, "info", message)
                continue
        match = _ANGEL.fullmatch(raw.strip()) or _COLON.fullmatch(raw.strip())
        if not match:
            if re.search(r"\b(error|warning|fatal)\b|\u043e\u0448\u0438\u0431\u043a", raw, re.I):
                unparsed.append(raw)
            continue
        try:
            line = int(match["line"])
            column = int(match["column"]) if match["column"] else None
        except ValueError:
            unparsed.append(raw)
            continue
        if line < 1 or (column is not None and column < 1):
            unparsed.append(raw)
            continue
        rest = match["rest"]
        severity = "error" if re.match(r"(?:error|fatal)\b", rest, re.I) else "warning" if re.match(r"warning\b", rest, re.I) else "unknown"
        message = re.sub(r"^(?:error|fatal|warning)\s*:\s*", "", rest, flags=re.I)
        append_diagnostic(match["file"], line, column, severity, message)
    return {"diagnostics": diagnostics, "truncated": False,
            "unparsed_truncated": False,
            "unparsed_error_lines": unparsed, "compiled": False,
            "compilation_state": "errors_reported" if any(d["severity"] == "error" for d in diagnostics) else "unknown",
            "compiler_api_available": False, "runtime_validation": "not_performed",
            "report_provenance": "caller_selected_workspace_file",
            "report_matches_runtime_compile": False}


def read_report(service, relative_package: str, expected_manifest_sha256: str | None,
                report_path: str, enable_ingame_tools: bool = False, *,
                show_script_errors: bool = False) -> dict:
    try:
        target = service.workspace._unprotected(_checked(service.workspace.root.joinpath(*_parts(report_path)), service.workspace.root))
        if target.suffix.lower() not in (".txt", ".log"):
            raise DevelopmentError("invalid_report_path", "A .txt or .log file inside the workspace is required")
        raw = _read_bytes(target, service.workspace.root, target.stat().st_size)
        text = raw.decode("utf-8-sig", errors="strict")
    except WorkspaceError as error:
        raise DevelopmentError(error.code, str(error)) from None
    except OSError:
        raise DevelopmentError("access_error", "Could not read the diagnostic report") from None
    except UnicodeError:
        raise DevelopmentError("invalid_report_encoding", "The diagnostic report must be UTF-8") from None
    manifest = service._manifest(relative_package, enable_ingame_tools=enable_ingame_tools,
                                 show_script_errors=show_script_errors)
    return {"package_name": manifest["package_name"], "manifest_sha256": manifest["manifest_sha256"],
            "report_sha256": hashlib.sha256(raw).hexdigest(),
            **parse_report(text, manifest["package_name"], manifest["files"]),
            "source_current_correlation": "unknown"}


def read_runtime_report(service, relative_package: str, expected_manifest_sha256: str | None, native_call,
                        *, enable_ingame_tools: bool = False, show_script_errors: bool = False) -> dict:
    native_report = native_call()
    text = native_report["text"]
    manifest = service._manifest(relative_package, enable_ingame_tools=enable_ingame_tools,
                                 show_script_errors=show_script_errors)
    return {"package_name": manifest["package_name"], "manifest_sha256": manifest["manifest_sha256"],
            "report_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            **parse_report(text, manifest["package_name"], manifest["files"]),
            "text": text, "native_report": native_report, "process_id": native_report.get("process_id"),
            "source": native_report.get("source"), "status": native_report.get("status"),
            "compiler_api_available": True, "report_provenance": "native_compiler_formatter",
            "source_current_correlation": "unknown", "compiled": False,
            "report_matches_runtime_compile": False}
