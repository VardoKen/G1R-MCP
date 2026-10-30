"""Публичные MCP-вызовы управления разработкой с изолированным executor."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

import g1r_mcp.server as server_module
from g1r_mcp.development import CommandResult, DevelopmentService
from g1r_mcp.server import create_server


class MCPDevelopmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "tools").mkdir(parents=True)
        for name in ("game_process_state.ps1", "start_g1r_development.ps1",
                     "stop_g1r_development.ps1", "load_native.ps1"):
            (self.repository / "tools" / name).write_text("# isolated fixture", encoding="utf-8")
        dll = self.repository / "build" / "native" / "Release" / "G1R_MCP.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"isolated DLL bytes")
        self.dll_sha256 = hashlib.sha256(dll.read_bytes()).hexdigest()
        self.workspace = self.root / "workspace"
        self.modkit = self.root / "Modkit"
        self.game = self.root / "Game"
        for directory in (self.workspace, self.modkit, self.game):
            directory.mkdir()
        (self.modkit / "Script").mkdir()
        (self.modkit / "Script" / "Fixture.as").write_text("class UFixture {}\n", encoding="utf-8")
        self.package = self.workspace / "Nested" / "Probe"
        (self.package / "Script").mkdir(parents=True)
        self.script = self.package / "Script" / "Probe.as"
        self.script.write_text("class UProbe {}\n", encoding="utf-8")
        (self.package / "GothicMod.json").write_text(json.dumps({"name": "Probe", "title": "Probe",
            "author": "Fixture", "modVersion": "1.0.0"}), encoding="utf-8")
        self.calls = []
        self.processes = []
        self.responses = {}
        self.service = DevelopmentService(repository_root=self.repository, workspace_root=self.workspace,
            modkit_root=self.modkit, game_root=self.game, executor=self.execute, powershell="fixture-powershell")
        self.identity = {"pid": 123, "creation_time": "2026-10-31T00:01:02.1234567Z",
                         "executable": str(self.service.inner_executable)}
        factory_patch = patch("g1r_mcp.server.DevelopmentService", return_value=self.service)
        self.factory = factory_patch.start()
        self.addCleanup(factory_patch.stop)
        self.server = create_server(self.modkit, self.workspace, game_root=self.game)

    def execute(self, arguments, timeout):
        self.calls.append((list(arguments), timeout))
        script = Path(arguments[arguments.index("-File") + 1]).name
        if script == "game_process_state.ps1":
            return CommandResult(0, json.dumps({"processes": self.processes}))
        if script in self.responses:
            return self.responses[script]
        if script == "start_g1r_development.ps1":
            value = {"state": "launch_requested", "package_name": "Probe"}
        elif script == "stop_g1r_development.ps1":
            value = {"state": "stopped", "process": self.identity}
        else:
            value = {"state": "loaded", "process": self.identity, "dll_sha256": self.dll_sha256}
        return CommandResult(0, json.dumps(value))

    def mutation_calls(self):
        return [(arguments, timeout) for arguments, timeout in self.calls
                if not arguments[arguments.index("-File") + 1].endswith("game_process_state.ps1")]

    async def tool(self, name, arguments=None):
        result = await self.server.call_tool(name, arguments or {})
        self.assertFalse(result.is_error, result.content)
        self.assertIsInstance(result.structured_content, dict)
        return result.structured_content

    async def error(self, name, arguments, code, *, unknown=False):
        with self.assertRaises(ToolError) as caught:
            await self.server.call_tool(name, arguments)
        error = json.loads(str(caught.exception)[str(caught.exception).index("{"):])
        self.assertEqual(error["code"], code)
        self.assertIs(error["outcome_unknown"], unknown)
        self.assertEqual(error["retry"], "only_explicit_new_request")
        return error

    async def test_recipe_start_load_stop_bind_hashes_and_process_identity(self):
        # Статический статус не создаёт сервис и не запускает системных команд.
        await self.tool("g1r_status")
        self.factory.assert_not_called()
        recipe = await self.tool("g1r_development_recipe", {"relative_package": "Nested/Probe"})
        self.assertFalse(recipe["compiled"])
        self.assertFalse(recipe["process_state"]["running"])
        self.assertEqual(recipe["native_artifact"]["sha256"], self.dll_sha256)
        manifest_hash = recipe["manifest"]["manifest_sha256"]
        start = await self.tool("g1r_development_start", {"relative_package": "Nested/Probe",
                               "expected_manifest_sha256": manifest_hash})
        self.assertEqual(start["state"], "launch_requested")
        self.assertEqual(start["manifest_sha256"], manifest_hash)
        self.assertFalse(start["compiled"])
        self.assertFalse(start["game_ready"])
        self.processes = [self.identity]
        processes = await self.tool("g1r_development_processes")
        self.assertEqual(processes["processes"], [self.identity])
        loaded = await self.tool("g1r_native_load", {"expected_process": self.identity,
                                                  "expected_dll_sha256": self.dll_sha256})
        self.assertEqual(loaded["dll_sha256"], self.dll_sha256)
        self.assertFalse(loaded["game_ready"])
        stopped = await self.tool("g1r_development_stop", {"expected_process": self.identity})
        self.assertEqual(stopped["process"], self.identity)
        self.assertFalse(stopped["outcome_unknown"])
        self.factory.assert_called_once_with(repository_root=Path(server_module.__file__).resolve().parents[2], workspace_root=self.workspace,
            modkit_root=self.modkit, game_root=self.game)
        mutations = self.mutation_calls()
        self.assertEqual([Path(args[args.index("-File") + 1]).name for args, _ in mutations],
                         ["start_g1r_development.ps1", "load_native.ps1", "stop_g1r_development.ps1"])
        self.assertTrue(all(timeout == 30 for _, timeout in self.calls))
        for args, _ in mutations[1:]:
            self.assertEqual(args[args.index("-ExpectedPid") + 1], "123")
            self.assertEqual(args[args.index("-ExpectedCreationTime") + 1], self.identity["creation_time"])
        self.assertEqual(mutations[1][0][mutations[1][0].index("-ExpectedDllSha256") + 1], self.dll_sha256)

    async def test_changed_manifest_process_and_dll_refuse_before_mutation(self):
        recipe = await self.tool("g1r_development_recipe", {"relative_package": "Nested/Probe"})
        self.script.write_text("class UChangedProbe {}\n", encoding="utf-8")
        await self.error("g1r_development_start", {"relative_package": "Nested/Probe",
                         "expected_manifest_sha256": recipe["manifest"]["manifest_sha256"]}, "package_changed")
        self.assertEqual(self.mutation_calls(), [])
        self.processes = [{**self.identity, "pid": 124}]
        await self.error("g1r_development_stop", {"expected_process": self.identity}, "process_changed")
        self.processes = [self.identity]
        await self.error("g1r_native_load", {"expected_process": self.identity,
                         "expected_dll_sha256": "0" * 64}, "dll_changed")
        current = await self.tool("g1r_development_recipe", {"relative_package": "Nested/Probe"})
        await self.error("g1r_development_start", {"relative_package": "Nested/Probe",
                         "expected_manifest_sha256": current["manifest"]["manifest_sha256"]}, "game_running")
        await self.error("g1r_development_start", {"relative_package": "Nested/Probe", "restart": True,
                         "expected_process": {**self.identity, "creation_time": "2026-10-31T00:01:02.0000000Z"},
                         "expected_manifest_sha256": current["manifest"]["manifest_sha256"]}, "process_changed")
        self.assertEqual(self.mutation_calls(), [])

    async def test_failed_mutation_reports_unknown_without_hidden_retry(self):
        recipe = await self.tool("g1r_development_recipe", {"relative_package": "Nested/Probe"})
        start = {"relative_package": "Nested/Probe", "expected_manifest_sha256": recipe["manifest"]["manifest_sha256"]}
        self.responses["start_g1r_development.ps1"] = CommandResult(1, "", "isolated failure")
        await self.error("g1r_development_start", start, "tool_failed", unknown=True)
        self.assertEqual(len(self.mutation_calls()), 1)
        # Следующая независимая проверка состояния не повторяет неясный запуск.
        await self.tool("g1r_development_processes")
        self.assertEqual(len(self.mutation_calls()), 1)
        self.responses["start_g1r_development.ps1"] = CommandResult(0, "not-json")
        await self.error("g1r_development_start", start, "tool_protocol_error", unknown=True)
        self.assertEqual(len(self.mutation_calls()), 2)
        self.responses.pop("start_g1r_development.ps1")
        explicit = await self.tool("g1r_development_start", start)
        self.assertEqual(explicit["state"], "launch_requested")
        self.assertEqual(len(self.mutation_calls()), 3)


if __name__ == "__main__":
    unittest.main()
