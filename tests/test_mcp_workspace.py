"""Сквозные проверки MCP-редактирования на отдельном временном пакете."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    async def test_package_creation_editing_conflicts_and_boundary_over_stdio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit = root / "Modkit"
            script = modkit / "Script/Official.as"
            script.parent.mkdir(parents=True)
            script.write_text("class UOfficial {}\n", encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            official_bytes = script.read_bytes()
            parameters = StdioServerParameters(command=sys.executable, args=[
                "-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit),
                "--workspace-root", str(workspace),
            ])
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    for name in ("g1r_create_package", "g1r_list_package_files", "g1r_read_package_file", "g1r_write_package_file"):
                        self.assertIn(name, tools)
                    self.assertFalse(tools["g1r_create_package"].annotations.read_only_hint)
                    self.assertFalse(tools["g1r_write_package_file"].annotations.read_only_hint)
                    self.assertTrue(tools["g1r_read_package_file"].annotations.read_only_hint)
                    metadata = {"name": "Example", "title": "Пример", "author": "Автор", "modVersion": "1.0.0-dev.1"}
                    created = await client.call_tool("g1r_create_package", {"relative_path": "Example", "metadata": metadata})
                    self.assertFalse(created.is_error)
                    self.assertTrue(created.structured_content["created"])
                    self.assertFalse(created.structured_content["compiled"])
                    self.assertEqual(created.structured_content["runtime_validation"], "not_performed")
                    write = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "Example", "file_path": "Script/Example.as", "content": "// первая строка\n// вторая\n",
                    })
                    self.assertFalse(write.is_error)
                    file = workspace / "Example/Script/Example.as"
                    self.assertEqual(write.structured_content["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
                    read = await client.call_tool("g1r_read_package_file", {
                        "relative_path": "Example", "file_path": "Script/Example.as", "start_line": 2, "line_count": 1,
                    })
                    self.assertFalse(read.is_error)
                    self.assertEqual(read.structured_content["text"], "// вторая")
                    expected = read.structured_content["sha256"]
                    replace = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "Example", "file_path": "Script/Example.as", "content": "// обновлено\n",
                        "expected_sha256": expected,
                    })
                    self.assertFalse(replace.is_error)
                    self.assertFalse(replace.structured_content["created"])
                    preserved = file.read_bytes()
                    stale = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "Example", "file_path": "Script/Example.as", "content": "overwrite",
                        "expected_sha256": expected,
                    })
                    self.assertTrue(stale.is_error)
                    self.assertIn("conflict", repr(stale.content))
                    self.assertEqual(file.read_bytes(), preserved)
                    for arguments in (
                        {"relative_path": "../Modkit", "file_path": "Script/Official.as", "content": "overwrite"},
                        {"relative_path": "Example", "file_path": "../GothicMod.json", "content": "overwrite"},
                        {"relative_path": "Example", "file_path": "Content/private.dat", "content": "overwrite"},
                    ):
                        denied = await client.call_tool("g1r_write_package_file", arguments)
                        self.assertTrue(denied.is_error)
                        self.assertNotIn(str(root), repr(denied))
                    duplicate = await client.call_tool("g1r_create_package", {"relative_path": "Example", "metadata": metadata})
                    self.assertTrue(duplicate.is_error)
                    invalid = await client.call_tool("g1r_create_package", {"relative_path": "Broken", "metadata": {"name": "Broken"}})
                    self.assertTrue(invalid.is_error)
                    self.assertFalse((workspace / "Broken").exists())
                    listing = await client.call_tool("g1r_list_package_files", {"relative_path": "Example"})
                    self.assertFalse(listing.is_error)
                    self.assertEqual([item["path"] for item in listing.structured_content["items"]],
                                     ["GothicMod.json", "Script/Example.as"])
                    validation = await client.call_tool("g1r_validate_package", {"relative_path": "Example"})
                    self.assertTrue(validation.structured_content["valid_structure"])
                    self.assertEqual(validation.structured_content["script_count"], 1)
            self.assertEqual(script.read_bytes(), official_bytes)
            self.assertFalse(any(path.name.startswith(".g1r-mcp-") for path in root.rglob("*")))
            self.assertEqual(json.loads((workspace / "Example/GothicMod.json").read_text(encoding="utf-8")), metadata)

    async def test_workspace_ancestor_allows_sibling_and_blocks_modkit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit = root / "Modkit"
            official = modkit / "Script/Official.as"
            official.parent.mkdir(parents=True)
            official.write_text("class UOfficial {}", encoding="utf-8")
            original = official.read_bytes()
            parameters = StdioServerParameters(command=sys.executable, args=[
                "-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit),
                "--workspace-root", str(root),
            ])
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    metadata = {"name": "Sibling", "title": "Соседний пакет", "author": "Автор", "modVersion": "1.0.0"}
                    created = await client.call_tool("g1r_create_package", {"relative_path": "Sibling", "metadata": metadata})
                    self.assertFalse(created.is_error)
                    sibling = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "Sibling", "file_path": "Script/Sibling.as", "content": "// sibling",
                    })
                    self.assertFalse(sibling.is_error)
                    for tool_name, arguments in (
                        ("g1r_list_package_files", {"relative_path": "Modkit"}),
                        ("g1r_read_package_file", {"relative_path": "Modkit", "file_path": "Script/Official.as"}),
                        ("g1r_write_package_file", {"relative_path": "Modkit", "file_path": "Script/New.as", "content": "new"}),
                        ("g1r_create_package", {"relative_path": "Modkit/Nested", "metadata": metadata}),
                    ):
                        denied = await client.call_tool(tool_name, arguments)
                        self.assertTrue(denied.is_error)
                        self.assertIn("unsafe_path", repr(denied.content))
                        self.assertNotIn(str(root), repr(denied))
            self.assertEqual(official.read_bytes(), original)
            self.assertFalse((modkit / "Script/New.as").exists())
            self.assertFalse((modkit / "Nested").exists())

    async def test_script_patch_workspace_flow_reports_structural_validation_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            modkit = root / "Modkit"
            source = modkit / "Script/Test.as"
            source.parent.mkdir(parents=True)
            source.write_text("old\n", encoding="utf-8")
            original = source.read_bytes()
            workspace = root / "workspace"
            workspace.mkdir()
            parameters = StdioServerParameters(command=sys.executable, args=[
                "-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit), "--workspace-root", str(workspace),
            ])
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    metadata = {"name": "PatchExample", "title": "Пакет патча", "author": "Автор", "modVersion": "1.0.0"}
                    created = await client.call_tool("g1r_create_package", {"relative_path": "PatchExample", "metadata": metadata})
                    self.assertFalse(created.is_error)
                    diff = "--- a/Script/Test.as\n+++ b/Script/Test.as\n@@ -1 +1 @@\n-old\n+new\n"
                    written = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "PatchExample", "file_path": "ScriptPatches/Changes.patch", "content": diff,
                    })
                    self.assertFalse(written.is_error)
                    read = await client.call_tool("g1r_read_package_file", {
                        "relative_path": "PatchExample", "file_path": "ScriptPatches/Changes.patch",
                    })
                    self.assertEqual(read.structured_content["sha256"], written.structured_content["sha256"])
                    self.assertEqual(read.structured_content["text"], diff.rstrip("\n"))
                    self.assertFalse(read.structured_content["compiled"])
                    validation = await client.call_tool("g1r_validate_package", {"relative_path": "PatchExample"})
                    self.assertTrue(validation.structured_content["valid_structure"])
                    self.assertEqual(validation.structured_content["patch_count"], 1)
                    self.assertEqual(validation.structured_content["patch_validation"], "not_performed")
                    listing = await client.call_tool("g1r_list_package_files", {"relative_path": "PatchExample"})
                    self.assertEqual([item["path"] for item in listing.structured_content["items"]],
                                     ["GothicMod.json", "ScriptPatches/Changes.patch"])
                    for file_path in ("ScriptPatches/Nested.patch.json", "ScriptPatches/nested/Changes.patch"):
                        denied = await client.call_tool("g1r_write_package_file", {
                            "relative_path": "PatchExample", "file_path": file_path, "content": diff,
                        })
                        self.assertTrue(denied.is_error)
                    stale = await client.call_tool("g1r_write_package_file", {
                        "relative_path": "PatchExample", "file_path": "ScriptPatches/Changes.patch", "content": diff + "\n",
                        "expected_sha256": "0" * 64,
                    })
                    self.assertTrue(stale.is_error)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(any(path.name.startswith(".g1r-mcp-") for path in root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
