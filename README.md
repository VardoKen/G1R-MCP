# G1R MCP

G1R MCP 1.0.0 connects MCP clients to Gothic 1 Remake for researching, developing, and debugging official mods built with `GothicMod.json` and AngelScript.

It provides tools to:

- Search Modkit source code and API declarations.
- Create and edit mod packages, prepare ScriptPatches, and inspect compiler diagnostics.
- Inspect game objects and reflected functions, read state, compare snapshots, and monitor selected properties.
- Call supported reflected functions and use the AngelScript debugger.
- Manage a development game session and load saves from the main menu.

One local Streamable HTTP server serves all connected clients. Optional protection lets a client reserve selected operations while other work remains shared.

## Requirements

- Windows x64 and an installed Steam copy of Gothic 1 Remake.
- Python 3.11 or later and the Microsoft Visual C++ x64 runtime.
- The official [G1R Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit).
- An MCP client that supports Streamable HTTP.

## Included components

The repository contains the Python MCP server, Windows launch tools, ready-to-use native DLL and loader in `build/native/Release/`, and an AngelScript development helper. The game and Modkit are obtained separately. No additional delivery archive is required.

## Getting started

Follow the [installation guide](docs/install.md) to install the server, configure your paths, and connect your MCP client. Give an AI agent the [agent guide](docs/agents.md) for tool usage and the game development workflow.

The shared MCP endpoint is `http://127.0.0.1:27124/mcp`. Start with `g1r_status` and `g1r_capabilities` to verify the connection. These tools report the server and its capabilities; they do not start the game or confirm live game readiness.

## License

G1R MCP is licensed under the [MIT License](LICENSE). Third-party components retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
