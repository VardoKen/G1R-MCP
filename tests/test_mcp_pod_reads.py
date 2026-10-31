"""Страницы массивов и копии структур проходят публичный MCP без изменения формы."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.test_mcp_observations import SessionNativeFixture


class MCPPODReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_paged_array_struct_metadata_and_unsupported_layout_keep_owner_and_values(self):
        common = {"source": "live_native", "session_id": "fixture-session-1", "handle": "obj-pod"}
        page = {**common, "property_name": "Numbers", "consistency": "single_tick_sequential",
                "element_type": "IntProperty", "element_size": 4, "array_dim": 1,
                "total_count": 205, "offset": 200, "next_offset": None, "has_more": False,
                "truncated": False, "partial": False,
                "items": [{"index": index, "supported": True, "value": index * 3 - 7}
                          for index in range(200, 205)]}
        empty = {**common, "property_name": "EmptyNames", "consistency": "single_tick_sequential",
                 "element_type": "NameProperty", "element_size": 8, "array_dim": 1,
                 "total_count": 0, "offset": 0, "next_offset": None, "has_more": False,
                 "truncated": False, "partial": False, "items": []}
        struct = {**common, "property_name": "StoredVector",
                  "consistency": "copied_bytes_then_metadata_revalidated",
                  "struct_path": "/Script/ActualReflection.Vector", "properties_size": 24,
                  "min_alignment": 8, "partial": False, "truncated": False,
                  "components": [{"name": "Z", "type": "DoubleProperty", "element_size": 8,
                                  "offset": 16, "value": 3.75},
                                 {"name": "X", "type": "DoubleProperty", "element_size": 8,
                                  "offset": 0, "value": 1.25},
                                 {"name": "Y", "type": "DoubleProperty", "element_size": 8,
                                  "offset": 8, "value": -2.5}]}
        no_effect = {"executed": False, "may_have_executed": False, "do_not_retry": False}
        status = {"ready": True, "session_id": "fixture-session-1", "tick_count": 50}
        steps = [
            {"op": "read_array", "params": {"handle": "obj-pod", "property_name": "Numbers",
             "offset": 200, "limit": 200}, "result": page},
            {"op": "read_array", "params": {"handle": "obj-pod", "property_name": "EmptyNames",
             "offset": 0, "limit": 64}, "result": empty},
            {"op": "read_struct", "params": {"handle": "obj-pod", "property_name": "StoredVector"}, "result": struct},
            {"op": "read_struct", "params": {"handle": "obj-pod", "property_name": "NestedTransform"},
             "error": {"code": "unsupported_struct_layout", "message": "Структура не входит в подтверждённый POD-контракт",
                       "details": no_effect}},
            {"op": "status", "params": {}, "result": status},
        ]
        with tempfile.TemporaryDirectory() as temporary, SessionNativeFixture(steps) as native:
            root = Path(temporary)
            modkit = root / "Modkit"
            (modkit / "Script").mkdir(parents=True)
            (modkit / "Script" / "Fixture.as").write_text("class UFixture {}\n", encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            parameters = StdioServerParameters(command=sys.executable, args=[
                "-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit),
                "--workspace-root", str(workspace), "--native-port", str(native.port)])
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    for name in ("g1r_read_array", "g1r_read_struct"):
                        annotations = tools[name].annotations
                        self.assertTrue(annotations.read_only_hint)
                        self.assertFalse(annotations.destructive_hint)
                        self.assertTrue(annotations.idempotent_hint)
                    for tool, params, expected in (
                        ("g1r_read_array", {"handle": "obj-pod", "property_name": "Numbers", "offset": 200, "limit": 200}, page),
                        ("g1r_read_array", {"handle": "obj-pod", "property_name": "EmptyNames"}, empty),
                        ("g1r_read_struct", {"handle": "obj-pod", "property_name": "StoredVector"}, struct),
                    ):
                        response = await client.call_tool(tool, params)
                        self.assertFalse(response.is_error, response.content)
                        self.assertEqual(response.structured_content, expected)
                    denied = await client.call_tool("g1r_read_struct", {"handle": "obj-pod", "property_name": "NestedTransform"})
                    self.assertTrue(denied.is_error, denied.content)
                    text = next(block.text for block in denied.content if block.type == "text")
                    error = json.loads(text[text.index("{"):])
                    self.assertEqual(error["code"], "unsupported_struct_layout")
                    self.assertIs(error["may_have_executed"], False)
                    self.assertEqual(error["effect"], no_effect)
                    self.assertEqual(error["request_id"], native.requests[3]["id"])
                    checked = await client.call_tool("g1r_native_status", {})
                    self.assertFalse(checked.is_error, checked.content)
                    self.assertEqual(checked.structured_content, status)
            self.assertEqual(native.connections, 1)
            self.assertEqual([request["op"] for request in native.requests],
                             ["read_array", "read_array", "read_struct", "read_struct", "status"])
            self.assertEqual(len({request["id"] for request in native.requests}), 5)


if __name__ == "__main__":
    unittest.main()
