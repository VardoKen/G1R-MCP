# G1R MCP guide for AI agents

G1R MCP 1.0.0 provides 64 MCP tools for researching, developing, and debugging official Gothic 1 Remake mods. Packages use `GothicMod.json`, AngelScript, and the official Modkit. An MCP client is the external application or host in which the AI agent runs and which connects to the G1R MCP server. This guide applies to any such host that supports Streamable HTTP. Human installation instructions are in [install.md](install.md).

The [G1R MCP repository](https://github.com/VardoKen/G1R-MCP) includes all of the bridge's own components: the Python MCP server, native DLL and loader, AngelScript development helper, and launch tools. The MCP client is the agent's host application, rather than an additional bridge component to obtain from this repository.

## Install and connect

The runtime requires Windows x64, Gothic 1 Remake installed through Steam, Python 3.11 or later, and the [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe). Obtain the [official G1R Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit), including its `Script/` directory. Consult the [official documentation](https://alkimiainteractive.github.io/G1R-Modkit/) for package and scripting rules.

Obtain [G1R MCP from GitHub](https://github.com/VardoKen/G1R-MCP). Keep the source tree and its supplied native files together:

- `build/native/Release/G1R_MCP.dll`
- `build/native/Release/g1r_mcp_loader.exe`
- `tools/` and `src/`

The supplied DLL and loader are ready to use; a native build is not required for installation. The repository uses the MIT license; see [LICENSE](../LICENSE) and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

From PowerShell in the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

Create or select an existing workspace for the mod packages. The following paths are examples; replace all three with the operator's actual folders:

```powershell
.\tools\start_shared_mcp.ps1 `
  -ModkitRoot 'C:\Modding\G1R-Modkit' `
  -WorkspaceRoot 'C:\Modding\MyMods' `
  -GameRoot 'C:\Games\Steam\steamapps\common\Gothic 1 Remake' `
  -Json
```

`ModkitRoot` contains `Script/`; `WorkspaceRoot` contains working packages; `GameRoot` is the installed game root containing `G1R/`. Keep these roles distinct. Workspace paths containing symlinks or junctions are rejected. The script's optional `-RepositoryRoot` selects the G1R MCP source tree and its `.venv` when launching the script from another location.

The launcher starts one background server and reuses its recorded process when called again with the same configuration. It does not launch the game or create an automatic startup entry. Configure every client with the same Streamable HTTP endpoint:

```text
http://127.0.0.1:27124/mcp
```

The endpoint is local to the Windows computer running the server; the host's MCP connection must be able to reach it on that computer. Use a URL connection in each client. A per-client command connection would create separate MCP processes. The shared server keeps one native TCP connection on `127.0.0.1:27123`, a shared debugger connection on port `27099`, and shared in-memory state for observations, save loading, and the debugger database. HTTP clients have separate MCP session identities while accessing these shared resources.

For a manually managed foreground server, the equivalent CLI is:

```powershell
.\.venv\Scripts\python.exe -m g1r_mcp.server `
  --transport streamable-http --host 127.0.0.1 --port 27124 `
  --modkit-root 'C:\Modding\G1R-Modkit' `
  --workspace-root 'C:\Modding\MyMods' `
  --game-root 'C:\Games\Steam\steamapps\common\Gothic 1 Remake' `
  --runtime-root 'C:\Modding\G1R-MCP' --native-port 27123
```

Here `--runtime-root` must be the actual repository root containing `build/native/Release/` and `tools/`. Choose either the shared launcher or this manual command for the endpoint. The CLI also supports `stdio`, which creates an independent server when a client launches it.

## Establish the current contract

Start with `g1r_status()` and `g1r_capabilities()`. Check the reported version, configured roots, and tool definitions. These two calls describe the server and implemented capabilities; they do not inspect the live game. A capability marked implemented can still be unavailable in the current game context. Use each tool's input schema and `tool_contracts` entry for its arguments, required state, result provenance, and suggested state tools.

The release tool inventory is:

| Area | Tools |
| --- | --- |
| Server and session metadata | `g1r_status`, `g1r_capabilities`, `g1r_session_status`, `g1r_session_acquire`, `g1r_session_release` |
| Source and API research | `g1r_source_identity`, `g1r_verify_source_game`, `g1r_find_source_files`, `g1r_search_source`, `g1r_read_source`, `g1r_read_source_exact`, `g1r_list_source_types`, `g1r_list_source_functions` |
| Workspace packages | `g1r_validate_package`, `g1r_create_package`, `g1r_create_package_directory`, `g1r_list_package_files`, `g1r_read_package_file`, `g1r_read_package_file_exact`, `g1r_write_package_file`, `g1r_export_package`, `g1r_verify_package_export`, `g1r_build_script_patch` |
| Development and native attachment | `g1r_compile_diagnostics`, `g1r_development_recipe`, `g1r_development_processes`, `g1r_development_start`, `g1r_development_stop`, `g1r_development_key`, `g1r_native_load`, `g1r_native_status`, `g1r_native_enable`, `g1r_apply_staged_file` |
| Runtime objects and values | `g1r_find_objects`, `g1r_object_properties`, `g1r_object_functions`, `g1r_read_array`, `g1r_read_struct`, `g1r_read_set`, `g1r_read_map`, `g1r_snapshot`, `g1r_compare_snapshots`, `g1r_invoke` |
| Observations | `g1r_watch_start`, `g1r_watch_read`, `g1r_watch_stop`, `g1r_watch_forget`, `g1r_watch_list`, `g1r_watch_resume` |
| AngelScript debugger | `g1r_debug_attach`, `g1r_debug_detach`, `g1r_debug_status`, `g1r_debug_breakpoints`, `g1r_debug_control`, `g1r_debug_stack`, `g1r_debug_variables`, `g1r_debug_evaluate`, `g1r_debug_database` |
| Saves and loading dialogs | `g1r_save_list`, `g1r_save_load`, `g1r_save_status`, `g1r_modlist_dialog`, `g1r_movieplayer_status`, `g1r_movieplayer_finish` |

Follow the operator's task and project rules. Tool availability, successful structural validation, and optional session metadata do not authorize launching a game, installing a mod, loading a save, exporting a ZIP, or changing gameplay by themselves. Existing task authorization remains valid across client reconnects.

## Research and edit before running the game

Use source and workspace tools independently of the game channel:

1. Read `g1r_source_identity()` to identify the Modkit checkout. `g1r_verify_source_game()` compares local Modkit scripts with installed game files; matching bytes do not prove an API's runtime behavior.
2. Find files with `g1r_find_source_files(query=...)`, then search literal source lines with `g1r_search_source(query=...)`. Their optional `relative_package` selects a workspace package instead of Modkit sources. Follow `next_offset` for subsequent pages.
3. Use `g1r_list_source_types` and `g1r_list_source_functions` to inspect declarations and prototypes. These are lexical indexes, not runtime signature validation. Source paths are relative to Modkit and use forward slashes, such as `Script/...` or `Examples/.../Script/...`; obtain exact paths from search results.
4. Use `g1r_read_source_exact(relative_path=...)` when bytes, BOM, line endings, or SHA-256 matter. For workspace files use `g1r_read_package_file_exact(relative_path=..., file_path=...)`.
5. Create or edit packages only within the selected workspace. `g1r_create_package` requires metadata fields `name`, `title`, `author`, and `modVersion`. Supported package components are `Script`, `ScriptPatches`, `Config`, `Content`, and `Story`.
6. Replace a text file through `g1r_write_package_file(relative_path=..., file_path=..., content=..., expected_sha256=...)` using the hash just read. Omitting the hash creates a new file and does not permit overwriting an existing file. `g1r_build_script_patch(source_path=..., replacement_content=..., expected_source_sha256=...)` returns a verified unified diff without writing it.
7. Run `g1r_validate_package(relative_path=...)`. It validates the descriptor and package structure; compilation and gameplay verification require separate evidence.

Use [the Modkit API reference](https://alkimiainteractive.github.io/G1R-Modkit/script-reference/index.html) and [Unreal AngelScript documentation](https://angelscript.hazelight.se/) alongside compatible source files. Establish class, property, function, and asset names from these sources or current runtime reflection. UE4SS/Lua results do not establish an official AngelScript contract.

## Start or reuse a development session

Reuse an appropriate running game for related checks. The bridge does not require a new game instance for every test. The following route assumes the task already permits development launch and native attachment.

1. Call `g1r_development_recipe(relative_package=...)`. Its `manifest.manifest_sha256` identifies the selected package composition; `native_artifact.sha256` identifies the supplied DLL. It reports launch configuration without starting the game. If the DLL is unavailable, correct the runtime installation first.
2. Call `g1r_development_processes()` and retain the returned `pid`, `creation_time`, and `executable` for the internal game process. The bootstrapper is a different process. Use the exact returned identity rather than a guessed PID or an executable name alone.
3. If an authorized new launch is needed, call `g1r_development_start(relative_package=..., expected_manifest_sha256=...)`. Optional `companion_packages`, `enable_ingame_tools`, and `show_script_errors` must match the recipe used for that hash. A running game is rejected unless `restart=true` and `expected_process` is supplied. A `launch_requested` result confirms the request, not successful compilation or readiness.
4. After launch, read `g1r_development_processes()` again to obtain the new internal process identity. Start creates working staging copies; source packages remain the canonical editable files.
5. Call `g1r_native_load(expected_process=..., expected_dll_sha256=...)` once with the selected identity and DLL hash. Read `g1r_native_status()` for the current connection, process, native session, installed hook, and readiness. A completed DLL load does not establish an active game Tick.
6. If the DLL reports `hook_installed=false` and enabling the bridge is within the task, call `g1r_native_enable()` after the main menu appears. Read `g1r_native_status()` again; object inspection and ordinary game calls require current Tick readiness.
7. Read compiler diagnostics using `g1r_compile_diagnostics(relative_package=..., expected_manifest_sha256=...)` for the actual selected or staged package. `report_path` instead selects an existing report file under the workspace. Interpret the reported `compiled` state and diagnostic provenance; absence of errors alone is not proof that a script compiled or completed.
8. Find an object, inspect its reflected signature, and make only the game call needed for the task. Check the resulting state independently.

For an already running compatible session, begin with process identification and `g1r_native_status()`. Attach the DLL only if necessary. Package activation is determined by the running development composition; installing files or obtaining a recipe does not change a running session's package selection.

## Inspect and invoke through reflection

`g1r_find_objects(query=..., class_query=..., exact_path=...)` returns opaque handles from the current native session. Inspect selected properties with `g1r_object_properties(handle=..., property_names=[...])`; inspect functions with `g1r_object_functions(handle=..., function_query=...)`.

For an invocation, select an exact returned function and its current `signature_token`:

```text
g1r_invoke(
  handle=<current object handle>,
  function=<exact reflected function name>,
  arguments={<exact parameter name>: <supported typed value>},
  expected_signature_token=<token returned by g1r_object_functions>
)
```

This is a template, not a callable example. Replace every placeholder with verified current data. Refresh reflection after hot reload or other signature changes. Object descriptors in arguments use `{"handle": "..."}`; pure output parameters use `{"out": true}` where supported. JSON types, exact field names, parameter direction, and reflected layout matter.

Read the current `invoke_arguments` and `invoke_returns` capability sections for the full encoding contract. Supported families include scalars, strings, names, object/class handles, selected engine structures, own scalar DTOs, and supported array element types. Support varies by input, const-reference input, pure output, and return direction. `inout` parameters are unsupported. Plain DTO invocation supports own reflected fields and nested scalar structures, but not string fields or custom lifecycle behavior. FString accepts Unicode but rejects embedded NUL. Invalid or unsupported signatures are rejected; finding a function name does not establish that it can be invoked through the bridge.

For value inspection, `g1r_read_struct` and structure elements in `g1r_read_array` can expose nested structures, strings, and arrays with per-child support/error information. This read contract is broader than the plain DTO invocation contract. `g1r_read_set` supports `Set<Name>`; `g1r_read_map` supports `Map<Name,Object>`. Their offsets count occupied slots, and `sparse_index` identifies the physical slot. Multiple pages are not one atomic snapshot. Check `partial`, `truncated`, `supported`, and error fields before treating a result as complete.

`g1r_snapshot(targets=[{"handle": "...", "properties": ["..."]}])` copies selected values on one Tick. `g1r_compare_snapshots(before=..., after=...)` compares caller-supplied copies without game access. Retain provenance and session identity when interpreting changes.

Generic execution of arbitrary AngelScript source, an Unreal editor, asset cooking, and Steam Workshop publishing are not implemented by this bridge. `g1r_debug_evaluate` evaluates an expression in a debugger frame; it is a separate feature and can have effects. Follow the official Modkit workflow for cooking and publication. Capability pagination defaults are request sizes, not universal engine or API limits.

## Hot reload, debugger, saves, and loading

For a function-body edit in an active staged package:

1. Read the canonical and staged file with `g1r_read_package_file_exact`, retaining both hashes.
2. Write the canonical file using its expected hash.
3. Call `g1r_apply_staged_file(relative_package=..., file_path=..., expected_source_sha256=..., expected_staged_sha256=..., expected_process=...)`. This accepts only `Script/**/*.as` files and applies one file to its recorded active staging package.
4. Use the returned staging path, refresh its recipe/hash, inspect compiler diagnostics, refresh the function signature, and verify behavior through the current object. A successful write does not confirm hot reload or compilation. Structural changes may require a separately authorized restart.

The debugger has one shared connection. `g1r_debug_attach()` attaches it; `g1r_debug_breakpoints(path=..., lines=[...], module=...)` replaces breakpoints for that explicit script and module. Inspect `g1r_debug_status`, then `g1r_debug_stack` and `g1r_debug_variables(expression=...)` for stopped execution. `g1r_debug_evaluate(expression=..., frame_id=...)` uses a frame ID from the current stack. `g1r_debug_control(action=...)` accepts `pause`, `continue`, `step_over`, `step_in`, or `step_out`. `g1r_debug_detach()` stops debugging and closes the connection. These actions affect every client using it.

`g1r_debug_database(refresh=true)` explicitly reads the runtime debugger database. Subsequent `query`, `limit`, and `offset` requests without refresh use the shared cache and can work after the game closes. The database is a debugger catalog, not a guarantee that every entry is a callable UFunction or a complete source catalog for every custom package.

Use the existing menu and loading tools when their prerequisites are satisfied:

- `g1r_save_list(profile_id=2)` selects the profile in the main-menu interface and returns its save slots. It changes menu/profile state while leaving save files unchanged; it is not a passive read.
- `g1r_save_load(slot_name=..., profile_id=..., accept_mod_mismatch=..., timeout=...)` loads a slot returned by the list. Its default `accept_mod_mismatch` is `true`; select the intended policy explicitly. `g1r_save_status()` reads the shared load result and refreshes world readiness without loading again.
- `g1r_modlist_dialog(action="status", process_id=...)` inspects the mismatch dialog outside ordinary Tick. `action="continue"` explicitly publishes its Continue action; omit `process_id` only when the tool can identify one internal game process.
- `g1r_movieplayer_status()` reads the current wait snapshot without ordinary Tick. `g1r_movieplayer_finish(expected_wait_id=...)` requests completion of that exact observed wait when its prerequisites hold. Completion of the finish call, return from the wait, and ordinary Tick readiness are distinct; verify their reported states.

Use these tools before introducing keyboard or mouse automation for the same operation. `g1r_development_key(expected_process=..., virtual_key=..., expected_window=...)` can queue one virtual key for the confirmed game window outside Tick; the receipt does not prove that the game handled it. It is not a text-entry tool.

## Shared observations and optional protection

All clients share the native connection. Watches are shared by default: start with `g1r_watch_start(handle=..., properties=[...], duration_ms=..., interval_ms=..., capacity=...)`; read with `g1r_watch_read(watch_id=..., after_seq=..., limit=...)`. Keep the returned cursor and inspect gap/loss information. `g1r_watch_stop` stops future sampling while retaining events; `g1r_watch_forget` releases a completed observation. `g1r_watch_list` exposes metadata; `g1r_watch_resume` updates optional client metadata, with optional `expected_session_id` and `expected_client_owner`. Resume does not recreate a watch or extend its duration.

`g1r_session_acquire(reason=...)` without `protected_tools` records information only. Game control and watches do not require a session claim. If a particular task needs protection from parallel clients, explicitly select tools listed in `g1r_capabilities.parallel_work.protectable_tools`, for example:

```json
{
  "reason": "Inspecting the current development session",
  "protected_tools": ["g1r_development_stop", "g1r_development_key"]
}
```

Pass that object to `g1r_session_acquire`. Only the listed operations are protected from other MCP clients. Reads remain available; `g1r_modlist_dialog(action="status")` remains available even when its Continue action is protected. The server checks protection when admitting a dispatch and does not cancel previously admitted calls. A protected high-level tool can perform its internal operations; protection is applied to MCP tool dispatch rather than every underlying native command.

Read `g1r_session_status()` for `protection`, its `claim_id`, owner, reason, `protected_tools`, and `caller_owns_protection`. `session_protected` errors include a receipt identifying the protection and selected operation. An empty list is rejected. Omitting or passing null `protected_tools` does not remove existing protection. There is no automatic protection, timeout, PID ownership gate, or client priority.

Any client can explicitly call `g1r_session_release(expected_claim_id=...)`, using the current `protection.claim_id` when it needs a change check. Any client can explicitly replace protection with `g1r_session_acquire(reason=..., protected_tools=[...], takeover=true, expected_claim_id=...)`. `expected_claim_id` is checked against active protection, not the informational claim. Release does not stop the game. Disconnecting a client or stopping the game does not remove protection; a client reconnecting with a new MCP session identity must inspect it and use explicit release or takeover when needed. The state is in memory and is lost when the shared MCP server ends.

## Recover and finish the session

Distinguish an HTTP-client reconnect from a native-channel reconnect. If the shared server and native connection remain intact, another HTTP client can inspect shared watches and results. When the native session changes, discard old object handles and watch IDs, read `g1r_native_status()`, and discover current objects again.

For an error reporting `outcome_unknown`, `may_have_executed`, or `do_not_retry`, a lost response does not prove that the action failed. Retain its receipt/request identity, inspect the operation's suggested state tool and relevant game state, and do not automatically resend a mutating call. For a lost save-loading response use `g1r_save_status`; for a lost watch response inspect `g1r_watch_list` and `g1r_watch_read`; for launch, stop, or load inspect process/native status. A later action must be a deliberate new request based on the recovered state.

When the authorized development checks are complete, stop observations that no longer need sampling, detach the debugger if finished, and close a game session launched for the task with `g1r_development_stop(expected_process=..., reason=...)`. Use its current exact process identity and respect shared work. Read process status to confirm the result. Staging copies are retained for review. Explicitly release any optional protection when the work that needs it ends.

Package export is a separate operation: `g1r_export_package(relative_package=..., destination="artifacts/...")` creates an official package ZIP and SHA-256 manifest; `g1r_verify_package_export(relative_path="artifacts/...")` verifies an existing export. Export does not install, compile, cook, or publish the package. Use it only when packaging is part of the requested work.

The shared MCP server can remain available for other clients. To stop that server itself, use `tools/stop_shared_mcp.ps1 -Json` from its repository; this uses its recorded process identity and does not stop the game. To update the runtime, end any development game holding the DLL, stop the shared server, update the repository, run `.\.venv\Scripts\python.exe -m pip install .` again, restart with the chosen roots, and reconnect clients. Recheck `g1r_status` and `g1r_capabilities` after the update.
