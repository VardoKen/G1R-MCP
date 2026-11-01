"""Публичные HTTP-инструменты SourceCatalog сохраняют выбранные объёмы и строки."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx2
from mcp.server.transport_security import TransportSecuritySettings

from g1r_mcp.server import create_server


class HTTPSourceCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_public_catalog_accepts_large_page_query_line_and_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit, workspace = root / "Modkit", root / "workspace"
            (modkit / "Script").mkdir(parents=True)
            workspace.mkdir()
            expected_paths = [f"Script/Entry{index:03}.as" for index in range(205)]
            for relative in expected_paths:
                (modkit / relative).write_bytes(b"class UEntry {} // entry needle\r\n")
            query = "q" * 513
            full_line = query + "x" * (4097 - len(query))
            (modkit / "Script" / "Long.as").write_bytes(full_line.encode("utf-8") + b"\r\n")
            original = {path.relative_to(modkit).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in modkit.rglob("*.as")}
            with patch("g1r_mcp.server.NativeClient") as native_factory:
                native = native_factory.return_value
                server = create_server(modkit, workspace, transport="streamable-http")
                app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp",
                    stateless_http=False, json_response=True, max_request_body_size=sys.maxsize,
                    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                        allowed_hosts=["127.0.0.1:27124"], allowed_origins=["http://127.0.0.1:27124"]))
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

                async def tool(client, session, name, arguments):
                    response = await rpc(client, "tools/call", {"name": name, "arguments": arguments}, session)
                    result = response.json()["result"]
                    self.assertFalse(result.get("isError", False), result)
                    return result["structuredContent"]

                async with app.router.lifespan_context(app):
                    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                                 base_url="http://127.0.0.1:27124") as client:
                        initialized = await rpc(client, "initialize", {"protocolVersion": "2025-06-18",
                            "capabilities": {}, "clientInfo": {"name": "source-catalog-volume", "version": "1"}})
                        session = initialized.headers["mcp-session-id"]
                        headers = {"Mcp-Session-Id": session, "Accept": "application/json, text/event-stream"}
                        notification = await client.post("/mcp", headers=headers,
                            json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                        self.assertEqual(notification.status_code, 202, notification.text)
                        names = ("g1r_find_source_files", "g1r_search_source", "g1r_list_source_types")
                        listed = (await rpc(client, "tools/list", {}, session)).json()["result"]["tools"]
                        schemas = {item["name"]: item["inputSchema"] for item in listed}
                        capabilities = await tool(client, session, "g1r_capabilities", {})
                        contracts = {item["name"]: item for item in capabilities["tool_contracts"]}
                        for name in names:
                            self.assertEqual(contracts[name]["args"], schemas[name])
                            self.assertNotIn("maxLength", schemas[name]["properties"]["query"])
                            self.assertNotIn("maximum", schemas[name]["properties"]["limit"])
                            self.assertNotIn("maximum", schemas[name]["properties"]["offset"])
                            first = await tool(client, session, name, {"query": "entry", "limit": 201})
                            second = await tool(client, session, name, {"query": "entry", "limit": 201, "offset": 201})
                            self.assertEqual([len(first["items"]), len(second["items"])], [201, 4])
                            self.assertEqual([item["path"] for page in (first, second) for item in page["items"]], expected_paths)
                            self.assertEqual(first["next_offset"], 201)
                            self.assertIsNone(second["next_offset"])
                            self.assertFalse(second["has_more"])
                            self.assertEqual(first["total_matches"], 205)
                            self.assertTrue(first["scan_complete"])
                            self.assertIsNone(first["max_offset"])
                            high = await tool(client, session, name, {"query": "entry", "limit": 201, "offset": 1000001})
                            self.assertEqual(high["items"], [])
                            self.assertEqual(high["offset"], 1000001)
                            self.assertEqual(high["total_matches"], 205)
                            self.assertIsNone(high["next_offset"])
                            self.assertFalse(high["has_more"])
                            self.assertFalse(high["pagination_limit_reached"])
                            self.assertIsNone(high["max_offset"])
                        text = await tool(client, session, "g1r_search_source", {"query": query, "limit": 201})
                        self.assertEqual(text["total_matches"], 1)
                        self.assertEqual(text["items"][0]["path"], "Script/Long.as")
                        self.assertEqual(text["items"][0]["text"], full_line)
                        self.assertEqual(len(text["items"][0]["text"]), 4097)
                        self.assertFalse(text["items"][0]["text_truncated"])
                        native.request.assert_not_called()
                        deleted = await client.delete("/mcp", headers=headers)
                        self.assertEqual(deleted.status_code, 200, deleted.text)
                        native.close.assert_not_called()
                native_factory.assert_called_once()
                native.close.assert_called_once_with()
            after = {path.relative_to(modkit).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in modkit.rglob("*.as")}
            self.assertEqual(after, original)
            self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
