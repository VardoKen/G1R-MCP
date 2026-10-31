"""Один HTTP-сервер разделяет рабочую базу и native-клиент между MCP-сессиями."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from mcp.server.transport_security import TransportSecuritySettings

from g1r_mcp.native_client import NativeClient
from g1r_mcp.server import create_server, main
from tests.test_mcp_observations import SessionNativeFixture


class MCPTransportCLITests(unittest.TestCase):
    def args(self, *extra):
        return ["--modkit-root", "fixture-modkit", "--workspace-root", "fixture-workspace", *extra]

    def test_stdio_is_default_and_does_not_receive_http_options(self):
        with patch("g1r_mcp.server.create_server") as factory:
            self.assertEqual(main(self.args()), 0)
            factory.return_value.run.assert_called_once_with(transport="stdio")

    def test_streamable_http_passes_exact_installed_sdk_options(self):
        for port in (27124, 29124):
            with self.subTest(port=port), patch("g1r_mcp.server.create_server") as factory:
                extra = ["--transport", "streamable-http"]
                if port != 27124:
                    extra += ["--port", str(port)]
                self.assertEqual(main(self.args(*extra)), 0)
                factory.assert_called_once()
                run = factory.return_value.run
                run.assert_called_once()
                options = run.call_args.kwargs
                self.assertEqual({key: value for key, value in options.items() if key != "transport_security"}, {
                    "transport": "streamable-http", "host": "127.0.0.1", "port": port,
                    "streamable_http_path": "/mcp", "stateless_http": False, "json_response": True})
                security = options["transport_security"]
                self.assertIsInstance(security, TransportSecuritySettings)
                self.assertIs(security.enable_dns_rebinding_protection, True)
                self.assertEqual(security.allowed_hosts, [f"127.0.0.1:{port}"])
                self.assertEqual(security.allowed_origins, [f"http://127.0.0.1:{port}"])

    def test_non_loopback_hosts_ports_and_unknown_transports_refuse_before_server(self):
        for extra in (
                ("--host", "0.0.0.0"), ("--host", "192.168.1.1"), ("--host", "localhost"),
                ("--host", "::1"), ("--port", "0"), ("--port", "1023"), ("--port", "65536"),
                ("--port", "text"), ("--transport", "sse")):
            with self.subTest(extra=extra), patch("g1r_mcp.server.create_server") as factory, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(self.args("--transport", "streamable-http", *extra))
                self.assertEqual(caught.exception.code, 2)
                factory.assert_not_called()


class MCPHTTPSharedServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_two_http_sessions_share_native_owner_workspace_and_security_boundary(self):
        native_status = {"ready": True, "session_id": "shared-native-owner", "tick_count": 31}
        steps = [
            {"op": "status", "params": {}, "result": native_status},
            {"op": "objects", "params": {"query": "Probe", "class_query": "", "limit": 50},
             "result": {"items": [{"handle": "obj-shared", "name": "Probe"}], "truncated": False}},
            {"op": "invoke", "params": {"handle": "obj-shared", "function": "ReadMarker", "arguments": {}},
             "result": {"return": 111}},
            {"op": "status", "params": {}, "result": native_status},
            {"op": "status", "params": {}, "result": native_status},
        ]
        with tempfile.TemporaryDirectory() as temporary, SessionNativeFixture(steps) as native:
            root = Path(temporary)
            modkit = root / "Modkit"
            (modkit / "Script").mkdir(parents=True)
            source = modkit / "Script" / "Fixture.as"
            source.write_text("class UFixture {}\n", encoding="utf-8")
            before = source.read_bytes()
            workspace = root / "workspace"
            workspace.mkdir()
            backend = NativeClient(port=native.port)
            self.addCleanup(backend.close)
            with patch("g1r_mcp.server.NativeClient", return_value=backend) as factory:
                server = create_server(modkit, workspace, native_port=native.port, transport="streamable-http")
                factory.assert_called_once_with(port=native.port)
            security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:27124"], allowed_origins=["http://127.0.0.1:27124"])
            app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp",
                stateless_http=False, json_response=True, transport_security=security)
            counter = 0

            async def rpc(client, method, params, session=None):
                nonlocal counter
                counter += 1
                headers = {"Accept": "application/json, text/event-stream"}
                if session:
                    headers["Mcp-Session-Id"] = session
                return await client.post("/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "id": counter, "method": method, "params": params})

            async def initialize(client):
                response = await rpc(client, "initialize", {"protocolVersion": "2025-06-18",
                    "capabilities": {}, "clientInfo": {"name": "isolated-http-client", "version": "1"}})
                self.assertEqual(response.status_code, 200, response.text)
                session = response.headers["mcp-session-id"]
                notified = await client.post("/mcp", headers={"Mcp-Session-Id": session,
                    "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                self.assertEqual(notified.status_code, 202, notified.text)
                return session

            async def tool(client, session, name, arguments=None):
                response = await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, session)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()["result"]
                self.assertFalse(result.get("isError", False), result)
                return result["structuredContent"]

            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                        base_url="http://127.0.0.1:27124") as first, \
                        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                        base_url="http://127.0.0.1:27124") as second:
                    first_session = await initialize(first)
                    second_session = await initialize(second)
                    self.assertNotEqual(first_session, second_session)
                    listed = await rpc(second, "tools/list", {}, second_session)
                    self.assertEqual(listed.status_code, 200, listed.text)
                    self.assertEqual(len(listed.json()["result"]["tools"]), 34)
                    self.assertEqual((await tool(second, second_session, "g1r_status"))["server"]["transport"], "streamable-http")
                    self.assertEqual(await tool(first, first_session, "g1r_native_status"), native_status)
                    found = await tool(first, first_session, "g1r_find_objects", {"query": "Probe"})
                    handle = found["items"][0]["handle"]
                    self.assertEqual(await tool(second, second_session, "g1r_invoke", {
                        "handle": handle, "function": "ReadMarker"}), {"return": 111})
                    metadata = {"name": "Shared", "title": "Общий пакет", "author": "Fixture", "modVersion": "1.0.0"}
                    await tool(first, first_session, "g1r_create_package", {"relative_path": "Shared", "metadata": metadata})
                    package = await tool(second, second_session, "g1r_read_package_file", {
                        "relative_path": "Shared", "file_path": "GothicMod.json"})
                    self.assertEqual(json.loads(package["text"]), metadata)
                    body = {"jsonrpc": "2.0", "id": 500, "method": "tools/call",
                            "params": {"name": "g1r_native_status", "arguments": {}}}
                    common = {"Accept": "application/json, text/event-stream", "Mcp-Session-Id": first_session}
                    host = await first.post("/mcp", json=body, headers={**common, "Host": "external.example:27124"})
                    self.assertEqual(host.status_code, 421, host.text)
                    origin = await first.post("/mcp", json=body, headers={**common, "Origin": "https://external.example"})
                    self.assertEqual(origin.status_code, 403, origin.text)
                    wrong_type = await first.post("/mcp", content=json.dumps(body),
                        headers={**common, "Content-Type": "text/plain"})
                    self.assertEqual(wrong_type.status_code, 400, wrong_type.text)
                    self.assertEqual(len(native.requests), 3)
                    self.assertEqual(await tool(second, second_session, "g1r_native_status"), native_status)
                    terminated = await first.delete("/mcp", headers=common)
                    self.assertEqual(terminated.status_code, 200, terminated.text)
                    self.assertEqual(await tool(second, second_session, "g1r_native_status"), native_status)
                    self.assertEqual((await first.post("/other", json=body, headers=common)).status_code, 404)
            self.assertEqual(native.connections, 1)
            self.assertEqual([request["op"] for request in native.requests], ["status", "objects", "invoke", "status", "status"])
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
