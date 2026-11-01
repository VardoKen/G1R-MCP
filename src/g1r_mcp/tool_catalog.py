"""Registered MCP-tool contracts without reading runtime state."""

from copy import deepcopy
from typing import Any, Iterable


# The fields below are an incomplete list of known successful-response fields,
# not a new required schema. Schemas and arguments come only from MCP registration.
_SOURCE_PAGE = ("items", "truncated", "total_matches", "source", "validation", "offset", "next_offset")
_WORKSPACE_FILE = ("source", "package_path", "file_path", "sha256", "text", "compiled", "runtime_validation")
_NATIVE_STATUS = ("version", "ready", "hook_installed", "tick_count", "requests", "pump")
_WATCH = ("watch_id", "session_id", "client_owner", "started_ms", "state", "reason", "cleanup_complete",
          "next_seq", "last_seq", "oldest_seq", "dropped_events", "capacity", "buffer_bytes", "buffer_limit_bytes")


def _entry(target: str, requires: tuple[str, ...], implementation: str,
           fields: tuple[str, ...], *, dynamic: bool = False,
           state_tools: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"target": target, "requires": requires, "implementation": implementation,
            "fields": fields, "dynamic": dynamic, "state_tools": state_tools}


_CONTRACTS = {
    "g1r_status": _entry("server_metadata", (), "src/g1r_mcp/server.py:g1r_status",
                          ("server", "sources", "workspace", "game_session", "game")),
    "g1r_capabilities": _entry("registered_tool_metadata", (), "src/g1r_mcp/server.py:g1r_capabilities",
                                ("catalog_revision", "tool_contracts", "local_tools", "native_tools", "workspace_tools")),
    "g1r_source_identity": _entry("modkit_git_metadata", ("configured_modkit",),
                                   "src/g1r_mcp/source_identity.py:inspect",
                                   ("inspected_at", "snapshot_kind", "source_only", "git", "game_compatibility_checked")),
    "g1r_verify_source_game": _entry("source_and_installed_game_bytes", ("configured_modkit", "installed_game_files"),
                                      "src/g1r_mcp/game_environment.py:verify_source_game",
                                      ("ok", "manifest", "scripts", "source_identity", "inspection_finished_at")),
    "g1r_find_source_files": _entry("modkit_or_workspace_package_source_files", ("configured_modkit_or_workspace",),
                                     "src/g1r_mcp/sources.py:SourceCatalog.search_files; src/g1r_mcp/workspace.py:PackageWorkspace.search_source_files", _SOURCE_PAGE),
    "g1r_search_source": _entry("modkit_or_workspace_package_source_text", ("configured_modkit_or_workspace",),
                                 "src/g1r_mcp/sources.py:SourceCatalog.search_text; src/g1r_mcp/workspace.py:PackageWorkspace.search_source_text", _SOURCE_PAGE),
    "g1r_read_source": _entry("modkit_source_text", ("configured_modkit",),
                               "src/g1r_mcp/sources.py:SourceCatalog.read_file", ("items", "path", "text", "sha256", "source")),
    "g1r_read_source_exact": _entry("modkit_source_bytes", ("configured_modkit",),
                                     "src/g1r_mcp/sources.py:SourceCatalog.read_file_exact",
                                     ("items", "path", "text", "sha256", "contains_bom", "source")),
    "g1r_list_source_types": _entry("lexical_source_types", ("configured_modkit",),
                                     "src/g1r_mcp/sources.py:SourceCatalog.list_types", _SOURCE_PAGE),
    "g1r_list_source_functions": _entry("lexical_source_functions", ("configured_modkit",),
                                         "src/g1r_mcp/sources.py:SourceCatalog.list_functions", _SOURCE_PAGE + ("parser_kind",)),
    "g1r_validate_package": _entry("workspace_package_structure", ("workspace_package",),
                                    "src/g1r_mcp/packages.py:validate_package",
                                    ("package_path", "valid_json", "valid_structure", "compiled", "runtime_validation", "metadata")),
    "g1r_create_package": _entry("workspace_package_files", ("configured_workspace", "windows_filesystem"),
                                  "src/g1r_mcp/workspace.py:PackageWorkspace.create_package",
                                  ("package_path", "created", "files", "directories", "sha256", "validation")),
    "g1r_create_package_directory": _entry("workspace_package_directories", ("workspace_package",),
                                            "src/g1r_mcp/workspace.py:PackageWorkspace.create_package_directory",
                                            ("package_path", "directory_path", "created", "created_paths", "created_count", "exists")),
    "g1r_list_package_files": _entry("workspace_package_files", ("workspace_package",),
                                      "src/g1r_mcp/workspace.py:PackageWorkspace.list_package_files",
                                      ("package_path", "items", "total", "offset", "next_offset", "has_more")),
    "g1r_read_package_file": _entry("workspace_package_text", ("workspace_package",),
                                     "src/g1r_mcp/workspace.py:PackageWorkspace.read_package_file", _WORKSPACE_FILE),
    "g1r_read_package_file_exact": _entry("workspace_package_bytes", ("workspace_package",),
                                           "src/g1r_mcp/workspace.py:PackageWorkspace.read_package_file_exact", _WORKSPACE_FILE),
    "g1r_write_package_file": _entry("workspace_package_files", ("workspace_package", "expected_file_hash_or_creation"),
                                      "src/g1r_mcp/workspace.py:PackageWorkspace.write_package_file",
                                      ("package_path", "file_path", "written", "created", "sha256", "bytes", "validation")),
    "g1r_export_package": _entry("official_package_export", ("workspace_package", "workspace_export_destination"),
                                   "src/g1r_mcp/package_export.py:export_package",
                                   ("destination", "archive", "archive_sha256", "manifest_sha256", "file_count",
                                    "total_source_bytes", "compiled", "installed", "runtime_validation")),
    "g1r_verify_package_export": _entry("official_package_export_archive", ("local_export_directory",),
                                          "src/g1r_mcp/package_export.py:verify_export",
                                          ("package_name", "file_count", "archive_sha256", "verified_files",
                                           "compiled", "installed", "runtime_validation")),
    "g1r_build_script_patch": _entry("returned_patch_text", ("configured_modkit",),
                                      "src/g1r_mcp/patches.py:ScriptPatchBuilder.build_script_patch",
                                      ("source", "path", "source_sha256", "replacement_sha256", "patch_text", "compiled")),
    "g1r_compare_snapshots": _entry("client_supplied_snapshot_copies", ("caller_supplied_snapshot_copies",),
                                     "src/g1r_mcp/snapshots.py:compare_snapshots",
                                     ("source", "validation", "provenance", "comparable", "items", "coverage", "truncated")),
    "g1r_session_status": _entry("shared_game_claim", (), "src/g1r_mcp/session_ownership.py:SessionOwnership.status",
                                  ("claimed", "claim", "disconnect_releases_claim", "automatic_game_stop")),
    "g1r_session_acquire": _entry("shared_game_claim", ("mcp_client_identity", "available_game_claim"),
                                   "src/g1r_mcp/session_ownership.py:SessionOwnership.acquire", ("claimed", "claim")),
    "g1r_session_release": _entry("shared_game_claim", ("own_claim_or_abandoned_claim_with_empty_process_snapshot",),
                                   "src/g1r_mcp/session_ownership.py:SessionOwnership.release",
                                   ("released", "previous_claim", "game_stopped")),
    "g1r_compile_diagnostics": _entry("compiler_diagnostic_text", ("workspace_package", "expected_manifest_hash",
                                                                "supplied_report_file_or_native_channel_and_angelscript_manager"),
                                       "src/g1r_mcp/compile_diagnostics.py:read_runtime_report/read_report",
                                       ("package_name", "manifest_sha256", "diagnostics", "compiled"),
                                       state_tools=("g1r_native_status",)),
    "g1r_development_recipe": _entry("package_and_process_metadata", ("workspace_package", "configured_game", "windows"),
                                       "src/g1r_mcp/development.py:DevelopmentService.recipe",
                                       ("manifest", "process_state", "native_artifact", "launch", "compiled")),
    "g1r_development_processes": _entry("windows_process_snapshot", ("configured_game", "windows"),
                                          "src/g1r_mcp/development.py:DevelopmentService.process_state",
                                          ("processes", "running", "source", "launch_observation")),
    "g1r_development_start": _entry("game_process_and_development_staging", ("workspace_package", "expected_manifest_hash",
                                                                          "windows", "http_game_claim", "exact_process_if_restart"),
                                      "src/g1r_mcp/development.py:DevelopmentService.start",
                                      ("state", "manifest_sha256", "package_name", "tool_result", "compiled", "outcome_unknown"),
                                      state_tools=("g1r_development_processes", "g1r_session_status")),
    "g1r_apply_staged_file": _entry("active_staging_file", ("workspace_package", "windows", "exact_expected_process", "http_game_claim"),
                                     "src/g1r_mcp/development.py:DevelopmentService.apply_staged_file",
                                     ("package_path", "file_path", "written", "created", "bytes", "sha256", "validation",
                                      "source_package_path", "staged_package_path", "source_sha256", "staged_before_sha256",
                                      "process", "launch_id", "source_modified", "hot_reload", "compiled", "runtime_validation"),
                                     state_tools=("g1r_development_processes", "g1r_session_status")),
    "g1r_development_stop": _entry("game_process", ("windows", "exact_expected_process", "http_game_claim"),
                                     "src/g1r_mcp/development.py:DevelopmentService.stop",
                                     ("state", "process", "tool_result", "outcome_unknown", "stop_reason"),
                                     state_tools=("g1r_development_processes", "g1r_session_status")),
    "g1r_development_key": _entry("game_window_message_queue", ("windows", "exact_expected_process",
                                                               "unique_or_exact_expected_window", "http_game_claim"),
                                    "src/g1r_mcp/window_input.py:queue_key",
                                    ("status", "process", "selected_hwnd", "window_thread_id", "virtual_key", "messages",
                                     "handling_confirmed", "outcome_unknown", "do_not_retry"),
                                    state_tools=("g1r_development_processes", "g1r_session_status")),
    "g1r_native_load": _entry("game_process_modules", ("windows", "exact_expected_process", "expected_dll_hash", "http_game_claim"),
                               "src/g1r_mcp/development.py:DevelopmentService.load_native",
                               ("state", "process", "dll_sha256", "game_ready", "outcome_unknown"),
                               state_tools=("g1r_development_processes", "g1r_session_status")),
    "g1r_native_status": _entry("native_process_metadata", ("native_tcp_channel",),
                                 "native/src/game_access.cpp:GameAccess::status", _NATIVE_STATUS),
    "g1r_native_enable": _entry("native_hooks_and_tick_queue", ("native_tcp_channel", "compatible_loaded_dll"),
                                 "native/src/game_access.cpp:GameAccess::start", _NATIVE_STATUS,
                                 state_tools=("g1r_native_status",)),
    "g1r_movieplayer_status": _entry(
        "movieplayer_wait_cache", ("native_tcp_channel",),
        "native/src/game_access.cpp:GameAccess::movieplayer_status",
        ("source", "active_wait", "wait_id", "wait_returned", "thread_id", "caller_allow_engine_tick",
         "entered_ms", "last_sample_ms", "sample_wait_id", "loading_finished", "movie_playing",
         "finish_call_completed", "last_returned_wait_id", "last_returned_ms", "sample_error",
         "hooks_installed", "wait_hook_installed", "message_pump_hook_installed", "adapter_source",
         "game_calls_on_status", "ordinary_tick_ready", "session_id", "process_id", "protocol")),
    "g1r_movieplayer_finish": _entry(
        "movieplayer_wait_finish", ("native_tcp_channel", "loaded_movieplayer_hooks", "matching_observed_wait_id",
                                  "loading_finished_at_platform_service", "http_game_claim"),
        "native/src/game_access.cpp:GameAccess::request_movieplayer_finish",
        ("source", "wait_id", "thread_id", "loading_finished", "finish_call_completed",
         "session_id", "process_id", "protocol"),
        state_tools=("g1r_movieplayer_status", "g1r_session_status")),
}

for _name, _implementation, _fields, _dynamic in (
    ("g1r_find_objects", "native/src/game_access.cpp:GameAccess::execute", ("items", "scanned_slots", "truncated", "thread_id"), False),
    ("g1r_object_properties", "native/src/game_access.cpp:GameAccess::properties", ("items", "truncated", "validation"), True),
    ("g1r_object_functions", "native/src/game_access.cpp:GameAccess::functions", ("items", "truncated", "scan_truncated", "scan_complete"), True),
    ("g1r_read_array", "native/src/game_access_array.cpp:GameAccess::read_array",
     ("source", "session_id", "handle", "items", "total_count", "offset", "next_offset", "has_more", "partial"), True),
    ("g1r_read_struct", "native/src/game_access_struct.cpp:GameAccess::read_struct",
     ("source", "session_id", "handle", "components", "partial", "truncated"), True),
    ("g1r_snapshot", "native/src/game_access.cpp:GameAccess::snapshot",
     ("snapshot_id", "source", "session_id", "objects", "partial", "truncated", "consistency"), True),
    ("g1r_invoke", "native/src/game_access.cpp:GameAccess::invoke",
     ("function", "executed", "return_type", "result", "thread_id"), True),
):
    _requires = ("native_tcp_channel", "current_game_tick")
    if _name != "g1r_find_objects":
        _requires += ("current_native_session_object_handles",)
    if _name == "g1r_invoke":
        _requires += ("reflected_function_signature", "function_specific_game_context")
    _CONTRACTS[_name] = _entry("game_function_effects" if _name == "g1r_invoke" else "runtime_reflection",
                               _requires, _implementation, _fields, dynamic=_dynamic,
                               state_tools=("g1r_native_status",))

for _name, _method, _fields in (
    ("g1r_watch_start", "start", _WATCH + ("start_seq", "duration_ms", "interval_ms", "sampling")),
    ("g1r_watch_read", "read", _WATCH + ("events", "gap", "cursor", "truncated")),
    ("g1r_watch_stop", "stop", _WATCH),
    ("g1r_watch_forget", "forget", ("watch_id", "forgotten", "cleanup_complete")),
):
    _requires = ("native_tcp_channel", "current_game_tick", "mcp_client_observation_owner")
    _requires += (("current_native_session_object_handles",) if _method == "start" else ("owned_watch_id",))
    _CONTRACTS[_name] = _entry("native_observation" if _method != "read" else "observation_event_copies",
                               _requires, "native/src/observations.cpp:Observations::" + _method,
                               _fields, dynamic=_method == "read", state_tools=("g1r_native_status",))

_CONTRACTS["g1r_watch_list"] = _entry(
    "current_native_connection_observation_metadata", ("native_tcp_channel", "current_game_tick", "mcp_client_identity"),
    "native/src/observations.cpp:Observations::list", ("session_id", "current_client_owner", "items"),
    dynamic=True, state_tools=("g1r_native_status",))
_CONTRACTS["g1r_watch_resume"] = _entry(
    "native_observation_owner", ("native_tcp_channel", "current_game_tick", "mcp_client_identity",
                                 "existing_watch_id", "expected_native_session_id", "expected_previous_client_owner"),
    "native/src/observations.cpp:Observations::resume", _WATCH, state_tools=("g1r_native_status", "g1r_watch_list"))


def build_tool_contracts(registered_tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Copy MCP schemas and descriptions without invoking handlers or probes."""
    contracts = []
    for tool in registered_tools:
        details = _CONTRACTS[tool.name]
        annotations = tool.annotations
        contracts.append({
            "name": tool.name,
            "description": tool.description,
            "args": deepcopy(tool.input_schema),
            "result": {"schema": deepcopy(tool.output_schema), "fields": list(details["fields"]),
                       "fields_complete": False, "dynamic_fields": details["dynamic"]},
            "effects": {"read_only": annotations.read_only_hint, "idempotent": annotations.idempotent_hint,
                        "destructive": annotations.destructive_hint, "target": details["target"]},
            "context": {"requires": list(details["requires"]), "state_tools": list(details["state_tools"])},
            "source": {"contract": "registered_mcp_tool", "handler": "src/g1r_mcp/server.py:" + tool.name,
                       "implementation": details["implementation"], "verification_kind": "source"},
            "implemented": True,
            "availability": {"checked": False, "current": None, "reason": "not_checked_by_catalog"},
        })
    return contracts
