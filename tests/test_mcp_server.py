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
