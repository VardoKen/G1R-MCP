# Install and run G1R MCP

## 1. Get the prerequisites

- Install Gothic 1 Remake through Steam.
- Install [Python 3.11 or later for Windows](https://www.python.org/downloads/windows/).
- Install the [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- Get the official [G1R Modkit repository](https://github.com/AlkimiaInteractive/G1R-Modkit). Keep its `Script/` directory and supporting files together.

An internet connection is needed to install the Python dependencies. The game, Modkit, mod workspace, and G1R MCP repository can be in separate folders.

## 2. Get and install the server

Clone the [G1R MCP repository](https://github.com/VardoKen/G1R-MCP):

```powershell
git clone https://github.com/VardoKen/G1R-MCP.git
cd G1R-MCP
```

You can also download the repository files from GitHub. Keep the repository layout intact. The native DLL and loader are already supplied in `build/native/Release/`; you do not need to compile them.

Open PowerShell in the G1R MCP repository folder. Check that `python --version` reports Python 3.11 or later, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

The second command installs the Python server and its dependencies. The environment does not need to be activated.

## 3. Choose your folders and start the server

Use the full path to each folder:

| Parameter | Folder |
| --- | --- |
| `ModkitRoot` | The official Modkit root containing `Script/` |
| `WorkspaceRoot` | Your working mod packages; create this folder if needed |
| `GameRoot` | The installed `Gothic 1 Remake` root containing `G1R/` |

From the G1R MCP repository folder, run the following command after replacing the example paths with your own:

```powershell
.\tools\start_shared_mcp.ps1 `
  -ModkitRoot 'C:\Modding\G1R-Modkit' `
  -WorkspaceRoot 'C:\Modding\MyMods' `
  -GameRoot 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake' `
  -Json
```

The command starts one local server in the background. Repeating it with the same configuration reuses the running server. Start it again after restarting Windows. Starting the MCP server does not launch the game.

## 4. Connect your MCP client

Add a **Streamable HTTP** MCP connection with this URL:

```text
http://127.0.0.1:27124/mcp
```

Use the same URL in every client or chat. Configure a URL connection, rather than a separate command that starts another server for each client.

Call `g1r_status`, then `g1r_capabilities`. Check the reported version and configured folders. This verifies the server connection and available tools. Live game readiness is checked separately with `g1r_native_status` after the development game and bridge have been started.

Give your AI agent [docs/agents.md](agents.md). It describes the tools, development launch, native loading, and optional protection for parallel work. Package development uses the official Modkit; this bridge does not use UE4SS.

## Stop or update

To stop the shared MCP server, run this command from the repository folder:

```powershell
.\tools\stop_shared_mcp.ps1 -Json
```

To update, stop the server and close any game session that loaded the bridge. Update the repository files, run `.\.venv\Scripts\python.exe -m pip install .` again, then start the server with your chosen folder paths. Closing the game releases the loaded DLL so it can be replaced.

If a connection fails, check that the server is running and the client uses the URL above. If startup reports a missing folder, correct the corresponding root path and run the start command again.
