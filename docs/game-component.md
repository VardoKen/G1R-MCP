# G1R MCP 1.0.0 game component

This package supplies the game-side files of [G1R MCP](https://github.com/VardoKen/G1R-MCP):
the native development bridge, its explicit loader, Windows game-session tools,
and AngelScript development helpers. The Python MCP server runs separately on
your Windows computer. Your AI application's MCP client connects to that server.

The internal package name remains `G1RMCPDevelopmentHelpers`. The game loads
its AngelScript from `Script/` when the package is enabled. The documented MCP
workflow explicitly loads the native DLL through `g1r_native_load`; installing
or enabling this package does not automatically load the DLL.

## Requirements

- Windows x64 and the Steam version of Gothic 1 Remake.
- Python 3.11 or later and the external G1R MCP 1.0.0 server from
  [GitHub](https://github.com/VardoKen/G1R-MCP).
- The [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- The [official G1R Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit)
  and an AI application with support for local Streamable HTTP MCP connections.

The supplied DLL and loader are ready to use. A C++ compiler and UE4SS are not
required. The Python server, AI application, [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit), and game are not included
in this game component.

## Package location

For manual installation, copy the `G1RMCPDevelopmentHelpers` folder into
`<Game>/G1R/Mods/` and enable **G1R MCP** in the game's mod management interface.
Keep the package layout intact:

```text
GothicMod.json
Script/G1RMCPDevelopmentHelpers.as
build/native/Release/G1R_MCP.dll
build/native/Release/g1r_mcp_loader.exe
tools/game_process_common.ps1
tools/game_process_state.ps1
tools/start_g1r_development.ps1
tools/stop_g1r_development.ps1
tools/load_native.ps1
tools/native_load_result.ps1
LICENSE
THIRD_PARTY_NOTICES.md
README.md
```

The normal Nexus/Vortex native-mod route places this folder under
`<Game>/G1R/Mods/`. Steam Workshop places the package root under
`<SteamLibrary>/steamapps/workshop/content/1297900/<WorkshopItemID>/`.
Use the actual installed folder on your computer as the runtime root below.
The Workshop item number belongs to the published package; do not substitute
the game App ID for it.

## Connect the external server

Install the Python server from the repository using its
[installation guide](https://github.com/VardoKen/G1R-MCP/blob/master/docs/install.md).
Run one manually managed server for all of your MCP clients. From PowerShell,
use the Python environment in which you installed G1R MCP:

```powershell
& 'C:\Modding\G1R-MCP\.venv\Scripts\python.exe' -B -m g1r_mcp.server `
  --transport streamable-http --host 127.0.0.1 --port 27124 --native-port 27123 `
  --modkit-root 'C:\Modding\G1R-Modkit' `
  --workspace-root 'C:\Modding\MyMods' `
  --game-root 'C:\Games\Steam\steamapps\common\Gothic 1 Remake' `
  --runtime-root 'C:\Games\Steam\steamapps\common\Gothic 1 Remake\G1R\Mods\G1RMCPDevelopmentHelpers'
```

These are example paths. Set all four roots to your own folders:

| Root | Contents |
|---|---|
| `modkit-root` | The official [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit), including `Script/` |
| `workspace-root` | Your working mod packages, outside the game and [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit) |
| `game-root` | The installed game directory containing `G1R/` |
| `runtime-root` | This installed package, containing `build/native/Release/` and `tools/` |

For a Workshop installation, replace only `runtime-root` with its installed
Workshop package root. This package does not contain the repository's shared
server launcher or a Python environment; use the direct command above when
selecting it as the runtime root.

Configure each AI application with the same MCP URL:

```text
http://127.0.0.1:27124/mcp
```

Give the AI agent the repository's
[agent guide](https://github.com/VardoKen/G1R-MCP/blob/master/docs/agents.md).
Begin with `g1r_status()` and `g1r_capabilities()`. These calls check the server
contract, not live game readiness. For an already running game, obtain its exact
identity with `g1r_development_processes()` and the supplied DLL's SHA-256 with
`g1r_development_recipe` for a selected workspace package. Explicitly call
`g1r_native_load(expected_process=..., expected_dll_sha256=...)`, then check
`g1r_native_status()` and enable the native channel as described in the agent guide.
Use current tool schemas and receipts for the required values.

## Helper in a development launch

The enabled installed package can supply its helper in an ordinary game session.
`g1r_development_start` instead creates a staging directory from explicitly
selected workspace packages and passes that directory as `-ModsDirectory`.
It does not automatically copy this installed helper into that composition.

To include the helper in such a session, copy only this package's `GothicMod.json`
and `Script/` into `<Workspace>/G1RMCPDevelopmentHelpers/`. Keep its internal name
unchanged. The DLL, loader, and tools remain at the installed runtime root.
For example, with a working package at `<Workspace>/MyMod/`, use:

```text
g1r_development_recipe(
  relative_package="MyMod",
  companion_packages=["G1RMCPDevelopmentHelpers"]
)

g1r_development_start(
  relative_package="MyMod",
  companion_packages=["G1RMCPDevelopmentHelpers"],
  expected_manifest_sha256=<hash returned by the recipe>
)
```

Use the same companion list and launch options for both calls. The hash and
package name above are placeholders for your actual recipe and working package.
A game launch or restart must be part of the operator's requested work; a running
game requires the explicit restart option and its exact process identity.

The helper exposes reflected text, world/player readiness, and line-trace
operations. Its world subsystem must exist in the live game before those
operations can be called. Installing files or preparing a recipe does not prove
successful compilation or activation.

## Updates and runtime behavior

Close a game session that has loaded the bridge before updating or removing its
files. Windows retains the loaded DLL until that game process exits. Running
this package does not require an additional Python server for every chat.

The DLL writes `native-phase.log` and `native-tick.log` beside itself in
`build/native/Release/`. These are generated diagnostics, not installation files.
The package contains no saves, game configuration changes, or UE4SS components.

The MCP bridge can change live game state when the agent invokes game operations.
Use it for the development actions you requested and interpret each operation's
reported result. The installation route does not provide automatic native DLL
loading through Steam Workshop.

## License

Original G1R MCP code is under the included MIT `LICENSE`. Incorporated native
libraries retain the notices in `THIRD_PARTY_NOTICES.md`.
