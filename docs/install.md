# Install and run G1R MCP

This repository includes the G1R MCP bridge: the Python MCP server, native DLL and loader, AngelScript development helper, and Windows launch tools. You install the supplied server on your computer and connect an AI application to it.

The **MCP client** is the AI application in which you work with your agent. The application must support MCP connections over **Streamable HTTP**. It is obtained separately; if you already use a compatible AI application, you can use it with G1R MCP. The server and its game connection code are included in this repository.

## 1. Get the prerequisites

- Install Gothic 1 Remake through Steam.
- Install [Python 3.11 or later for Windows](https://www.python.org/downloads/windows/).
- Install the [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- Get the official [G1R Modkit repository](https://github.com/AlkimiaInteractive/G1R-Modkit). Keep its `Script/` directory and supporting files together.
- Use an AI application with MCP support over Streamable HTTP, as described above.

An internet connection is needed to install the Python dependencies. The game, [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit), mod workspace, and G1R MCP repository can be in separate folders.

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
| `ModkitRoot` | The official [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit) root containing `Script/` |
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

## 4. Connect your AI application to the server

Open your AI application's MCP connection settings and add a **Streamable HTTP** connection to the G1R MCP server with this URL. The location and name of these settings depend on the application:

```text
http://127.0.0.1:27124/mcp
```

This address is local to the Windows computer running the server; your AI application's MCP connection must be able to reach it on that computer. Use the same URL in every application or chat that needs the bridge. The server was already started in step 3; configure the application to connect to that running server by URL. A connection configured to launch a server command would start a separate server for each application.

Ask your AI agent to call `g1r_status`, then `g1r_capabilities`, and check the reported version and configured folders. This verifies the server connection and available tools. Live game readiness is checked separately with `g1r_native_status` after the development game and bridge have been started.

Give your AI agent [docs/agents.md](agents.md). It describes the tools, development launch, native loading, and optional protection for parallel work. Package development uses the official [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit); this bridge does not use UE4SS.

## Stop or update

To stop the shared MCP server, run this command from the repository folder:

```powershell
.\tools\stop_shared_mcp.ps1 -Json
```

To update, stop the server and close any game session that loaded the bridge. Update the repository files, run `.\.venv\Scripts\python.exe -m pip install .` again, then start the server with your chosen folder paths. Closing the game releases the loaded DLL so it can be replaced.

If a connection fails, check that the server is running and your AI application's MCP connection uses the URL above. If startup reports a missing folder, correct the corresponding root path and run the start command again.
