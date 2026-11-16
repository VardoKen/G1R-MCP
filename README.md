# G1R MCP

G1R MCP 1.0.0 connects AI applications to Gothic 1 Remake through MCP (Model Context Protocol) for researching, developing, and debugging official mods built with `GothicMod.json` and AngelScript.

You work with your AI agent in an AI application. That application is called the **MCP client**: it connects to the **G1R MCP server** and calls the bridge's tools. This repository supplies all G1R MCP bridge components; you connect your chosen AI application to them.

It provides tools to:

- Search [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit) source code and API declarations.
- Create and edit mod packages, prepare ScriptPatches, and inspect compiler diagnostics.
- Inspect game objects and reflected functions, read state, compare snapshots, and monitor selected properties.
- Call supported reflected functions and use the AngelScript debugger.
- Manage a development game session and load saves from the main menu.

One local server serves all connected AI applications and chats through Streamable HTTP, the supported MCP connection type. Optional protection lets an agent reserve selected operations while other work remains shared.

## Requirements

- Windows x64 and an installed Steam copy of Gothic 1 Remake.
- Python 3.11 or later and the Microsoft Visual C++ x64 runtime.
- The official [G1R Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit).
- An AI application that supports MCP connections over Streamable HTTP and can connect to the local server on this Windows computer. If you already use a compatible AI application, use it to connect to G1R MCP.

## Included components

The repository contains the Python MCP server and its game connection code, Windows launch tools, ready-to-use native DLL and loader in `build/native/Release/`, and an AngelScript development helper. The AI application, game, [Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit), Python, and Visual C++ runtime are external requirements. Installing the Python server also installs its Python dependencies. No additional bridge download or delivery archive is required.

## Getting started

Follow the [installation guide](docs/install.md) to install the included server, configure your paths, and connect your AI application. Give an AI agent the [agent guide](docs/agents.md) for tool usage and the game development workflow.

The [game component guide](docs/game-component.md) explains the separate game-side package and how the external server uses its installed files.

The shared MCP endpoint is `http://127.0.0.1:27124/mcp`. Start with `g1r_status` and `g1r_capabilities` to verify the connection. These tools report the server and its capabilities; they do not start the game or confirm live game readiness.

## License

G1R MCP is licensed under the [MIT License](LICENSE). Third-party components retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
