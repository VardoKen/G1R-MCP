"""Реальная MCP stdio-сессия на изолированных источниках и пакетах."""

import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from g1r_mcp import __version__


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

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
                    self.assertEqual(len(names), 7)
                    self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))

                    status = await client.call_tool("g1r_status", {})
                    self.assertFalse(status.is_error)
                    self.assertTrue(status.structured_content["server"]["ready"])
                    self.assertFalse(status.structured_content["game"]["ready"])
                    self.assertEqual(status.structured_content["sources"]["revision_source"], "caller_label")

                    capabilities = await client.call_tool("g1r_capabilities", {})
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
