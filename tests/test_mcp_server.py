"""Реальная MCP stdio-сессия на изолированных источниках и пакетах."""

import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import httpx2
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.transport_security import TransportSecuritySettings

from g1r_mcp import __version__
from g1r_mcp.server import create_server


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_http_watch_tools_forward_hidden_session_owner_to_one_native_client(self):
        calls = (("g1r_watch_start", "watch_start", {"handle": "obj-1", "properties": ["Enabled"],
                  "duration_ms": 1000, "interval_ms": 20, "capacity": 2}),
                 ("g1r_watch_read", "watch_read", {"watch_id": "watch-1", "after_seq": 3, "limit": 10}),
                 ("g1r_watch_stop", "watch_stop", {"watch_id": "watch-1"}),
                 ("g1r_watch_forget", "watch_forget", {"watch_id": "watch-1"}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit, workspace = root / "Modkit", root / "workspace"
            (modkit / "Script").mkdir(parents=True)
            source = modkit / "Script" / "Fixture.as"
            source.write_bytes(b"class UFixture {}\r\n")
            original = source.read_bytes()
            workspace.mkdir()
            with patch("g1r_mcp.server.NativeClient") as factory:
                native = factory.return_value
                native.request.side_effect = lambda operation, parameters: {"operation": operation}
                server = create_server(modkit, workspace, native_port=27123, transport="streamable-http")
                security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                    allowed_hosts=["127.0.0.1:27124"], allowed_origins=["http://127.0.0.1:27124"])
                app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp",
                    stateless_http=False, json_response=True, transport_security=security)
                counter = 0

                async def rpc(client, method, params, session=None):
                    nonlocal counter
                    counter += 1
                    headers = {"Accept": "application/json, text/event-stream"}
                    if session is not None:
                        headers["Mcp-Session-Id"] = session
                    response = await client.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "id": counter, "method": method, "params": params})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response

                async def initialize(client, name):
                    response = await rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                               "clientInfo": {"name": name, "version": "1"}})
                    session = response.headers["mcp-session-id"]
                    notified = await client.post("/mcp", headers={"Mcp-Session-Id": session,
                        "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                    self.assertEqual(notified.status_code, 202, notified.text)
                    return session

                async with app.router.lifespan_context(app):
                    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1:27124") as first, \
                               httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1:27124") as second:
                        one, two = await initialize(first, "watch-owner-one"), await initialize(second, "watch-owner-two")
                        self.assertNotEqual(one, two)
                        listed = (await rpc(first, "tools/list", {}, one)).json()["result"]["tools"]
                        schemas = {item["name"]: item["inputSchema"] for item in listed}
                        for name, _, arguments in calls:
                            self.assertEqual(set(schemas[name]["properties"]), set(arguments))
                            self.assertNotIn("ctx", schemas[name]["properties"])
                            self.assertNotIn("client_owner", schemas[name]["properties"])
                        for client, session in ((first, one), (second, two)):
                            for name, operation, arguments in calls:
                                response = await rpc(client, "tools/call", {"name": name, "arguments": arguments}, session)
                                result = response.json()["result"]
                                self.assertFalse(result.get("isError", False), result)
                                self.assertEqual(result["structuredContent"], {"operation": operation})
                                native.request.assert_called_with(operation, {**arguments, "client_owner": "http:" + session})
                        self.assertEqual(native.request.call_count, 8)
                        first_owner = {item.args[1]["client_owner"] for item in native.request.call_args_list[:4]}
                        second_owner = {item.args[1]["client_owner"] for item in native.request.call_args_list[4:]}
                        self.assertEqual(first_owner, {"http:" + one})
                        self.assertEqual(second_owner, {"http:" + two})
                        self.assertTrue(first_owner.isdisjoint(second_owner))
                        for client, session in ((first, one), (second, two)):
                            deleted = await client.delete("/mcp", headers={"Mcp-Session-Id": session,
                                                        "Accept": "application/json, text/event-stream"})
                            self.assertEqual(deleted.status_code, 200, deleted.text)
                        factory.assert_called_once_with(port=27123)
                        native.close.assert_not_called()
                native.close.assert_called_once_with()
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(workspace.iterdir()), [])

    async def test_stdio_tools_sources_validation_and_errors(self):
        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        self.assertEqual(tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"], __version__)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit = root / "Modkit"
            script = modkit / "Script" / "Example.as"
            script.parent.mkdir(parents=True)
            script.write_text("class UExample : UObject\n{\n    FString Caption;\n}\n", encoding="utf-8")
            package = root / "workspace" / "Example"
            package.mkdir(parents=True)
            descriptor = package / "GothicMod.json"
            descriptor.write_text(json.dumps({
                "name": "Example", "title": "Пример", "author": "Автор", "modVersion": "1.0.0-dev.1",
            }, ensure_ascii=False), encoding="utf-8")
            before = (script.read_bytes(), descriptor.read_bytes())
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit),
                      "--workspace-root", str(root / "workspace"), "--source-revision", "a" * 40],
            )
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.server_info.name, "G1R MCP")
                    self.assertEqual(initialized.server_info.version, __version__)
                    tools = (await client.list_tools()).tools
                    names = {tool.name for tool in tools}
                    self.assertEqual(len(names), 42)
                    self.assertIn("g1r_development_key", names)
                    source_tools = {"g1r_source_identity", "g1r_verify_source_game", "g1r_list_source_functions"}
                    self.assertTrue(source_tools.issubset(names))
                    mutations = {"g1r_invoke", "g1r_native_enable", "g1r_create_package",
                                 "g1r_write_package_file", "g1r_watch_start", "g1r_watch_stop", "g1r_watch_forget",
                                 "g1r_development_start", "g1r_development_stop", "g1r_development_key", "g1r_native_load",
                                 "g1r_create_package_directory"}
                    mutations.update({"g1r_session_acquire", "g1r_session_release"})
                    self.assertTrue(all(tool.annotations.read_only_hint for tool in tools if tool.name not in mutations))
                    self.assertFalse(next(tool for tool in tools if tool.name == "g1r_invoke").annotations.read_only_hint)
                    key_tool = next(tool for tool in tools if tool.name == "g1r_development_key")
                    self.assertFalse(key_tool.annotations.read_only_hint)
                    self.assertFalse(key_tool.annotations.idempotent_hint)
                    for tool in tools:
                        if tool.name in {"g1r_watch_start", "g1r_watch_read", "g1r_watch_stop", "g1r_watch_forget"}:
                            self.assertNotIn("ctx", tool.input_schema["properties"])
                            self.assertNotIn("client_owner", tool.input_schema["properties"])

                    status = await client.call_tool("g1r_status", {})
                    self.assertFalse(status.is_error)
                    self.assertTrue(status.structured_content["server"]["ready"])
                    self.assertFalse(status.structured_content["game"]["ready"])
                    self.assertEqual(status.structured_content["sources"]["revision_source"], "caller_label")

                    capabilities = await client.call_tool("g1r_capabilities", {})
                    self.assertFalse(capabilities.is_error)
                    self.assertTrue(source_tools.issubset({tool["name"] for tool in
                                                         capabilities.structured_content["local_tools"]}))
                    helpers = capabilities.structured_content["development_tools"]["helpers"]
                    self.assertIn("ReadText", helpers["functions"])
                    self.assertIn("g1r_development_key", capabilities.structured_content["development_tools"]["operations"])
                    self.assertTrue(all(not group["available"] for group in capabilities.structured_content["game_groups"]))
                    found = await client.call_tool("g1r_find_source_files", {"query": "Example"})
                    self.assertFalse(found.is_error)
                    self.assertEqual(len(found.structured_content["items"]), 1)
                    read = await client.call_tool("g1r_read_source", {"relative_path": "Script/Example.as", "line_count": 2})
                    self.assertFalse(read.is_error)
                    search = await client.call_tool("g1r_search_source", {"query": "Caption"})
                    self.assertFalse(search.is_error)
                    types = await client.call_tool("g1r_list_source_types", {"query": "UExample"})
                    self.assertFalse(types.is_error)
                    self.assertEqual(len(types.structured_content["items"]), 1)
                    validation = await client.call_tool("g1r_validate_package", {"relative_path": "Example"})
                    self.assertFalse(validation.is_error)
                    self.assertTrue(validation.structured_content["valid_structure"])
                    self.assertEqual(validation.structured_content["runtime_validation"], "not_performed")

                    denied = await client.call_tool("g1r_read_source", {"relative_path": "../private.as"})
                    self.assertTrue(denied.is_error)
                    self.assertNotIn(str(root), repr(denied))
                    bad_limit = await client.call_tool("g1r_find_source_files", {"query": "x", "limit": 0})
                    self.assertTrue(bad_limit.is_error)
            self.assertEqual(before, (script.read_bytes(), descriptor.read_bytes()))


if __name__ == "__main__":
    unittest.main()
