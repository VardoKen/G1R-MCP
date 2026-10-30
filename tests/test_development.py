"""Изолированный контур разработки: подмена только исполнителя процессов."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from g1r_mcp.development import (CommandResult, DevelopmentError, DevelopmentService,
                                 execute_command, PROCESS_TIMEOUT_SECONDS)


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "tools").mkdir(parents=True)
        for name in ("game_process_state.ps1", "start_g1r_development.ps1",
                     "stop_g1r_development.ps1", "load_native.ps1"):
            (self.repository / "tools" / name).write_text("# isolated", encoding="utf-8")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.modkit = self.root / "modkit"
        self.modkit.mkdir()
        self.game = self.root / "game"
        self.game.mkdir()
        self.package = self.workspace / "Nested" / "Probe"
        (self.package / "Script").mkdir(parents=True)
        (self.package / "Script" / "Probe.as").write_text("class UProbe {}", encoding="utf-8")
        (self.package / "GothicMod.json").write_text(json.dumps({"name": "Probe", "title": "Probe",
                                                                 "author": "Test", "modVersion": "1.0.0"}), encoding="utf-8")
        self.calls = []
        self.processes = []
        self.responses = {}
        self.service = DevelopmentService(repository_root=self.repository, workspace_root=self.workspace,
                                           modkit_root=self.modkit, game_root=self.game,
                                           executor=self.execute, powershell="fixed-powershell")
        self.identity = {"pid": 123, "creation_time": "2026-10-30T01:02:03.1234567Z",
                         "executable": str(self.service.inner_executable)}

    def execute(self, arguments, timeout):
        self.calls.append((arguments, timeout))
        script = Path(arguments[arguments.index("-File") + 1]).name
        if script == "game_process_state.ps1":
            return CommandResult(0, json.dumps({"processes": self.processes}))
        if script in self.responses:
            return self.responses[script]
        if script == "start_g1r_development.ps1":
            result = {"state": "launch_requested", "package_name": "Probe"}
        elif script == "stop_g1r_development.ps1":
            result = {"state": "stopped", "process": self.identity}
        else:
            result = {"state": "loaded", "process": self.identity,
                      "dll_sha256": arguments[arguments.index("-ExpectedDllSha256") + 1]}
        return CommandResult(0, json.dumps(result))

    def recipe(self):
        return self.service.recipe("Nested/Probe")

    def test_recipe_hashes_full_package_without_runtime_claim(self):
        (self.package / "Content").mkdir()
        (self.package / "Content" / "asset.bin").write_bytes(b"Asset")
        result = self.recipe()
        manifest = result["manifest"]
        self.assertEqual([entry["path"] for entry in manifest["files"]],
                         ["Content/asset.bin", "GothicMod.json", "Script/Probe.as"])
        self.assertEqual(result["launch"]["mods_directory"], str(self.package.parent))
        self.assertEqual(result["launch"]["mods"], ["Probe"])
        self.assertFalse(result["compiled"])
        self.assertFalse(result["diagnostics_available"])
        self.assertFalse(result["process_state"]["running"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], 30)
        changed = manifest["manifest_sha256"]
        (self.package / "Content" / "asset.bin").write_bytes(b"Changed")
        self.assertNotEqual(self.recipe()["manifest"]["manifest_sha256"], changed)

    def test_start_binds_recipe_hash_and_requests_only_fixed_command(self):
        recipe = self.recipe()
        result = self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"])
        self.assertEqual(result["state"], "launch_requested")
        self.assertFalse(result["compiled"])
        self.assertFalse(result["game_ready"])
        arguments = self.calls[-1][0]
        self.assertEqual(arguments[0], "fixed-powershell")
        self.assertEqual(arguments[arguments.index("-Mods") + 1], "Probe")
        self.assertEqual(arguments[arguments.index("-ModsDirectory") + 1], str(self.package.parent))
        self.assertEqual(arguments[-1], "-Json")
        (self.package / "Script" / "Probe.as").write_text("class UChanged {}", encoding="utf-8")
        before = len(self.calls)
        with self.assertRaises(DevelopmentError) as captured:
            self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"])
        self.assertEqual(captured.exception.code, "package_changed")
        self.assertEqual(len(self.calls), before)

    def test_restart_requires_current_exact_process_identity(self):
        self.processes = [self.identity]
        recipe = self.recipe()
        with self.assertRaises(DevelopmentError) as captured:
            self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"])
        self.assertEqual(captured.exception.code, "game_running")
        for identity in (None, {**self.identity, "pid": 124},
                         {**self.identity, "creation_time": "2026-10-30T01:02:03.0000000Z"}):
            with self.subTest(identity=identity), self.assertRaises(DevelopmentError):
                self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"],
                                   restart=True, expected_process=identity)
        result = self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"],
                                    restart=True, expected_process=self.identity)
        self.assertEqual(result["state"], "launch_requested")
        arguments = self.calls[-1][0]
        self.assertIn("-Restart", arguments)
        self.assertEqual(arguments[arguments.index("-ExpectedPid") + 1], "123")
        self.assertEqual(arguments[arguments.index("-ExpectedCreationTime") + 1], self.identity["creation_time"])

    def test_stop_and_load_require_identity_and_load_requires_dll_hash(self):
        self.processes = [self.identity]
        result = self.service.stop(self.identity)
        self.assertEqual(result["process"], self.identity)
        dll = self.repository / "build" / "native" / "Release" / "G1R_MCP.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"isolated dll")
        digest = hashlib.sha256(dll.read_bytes()).hexdigest()
        with self.assertRaises(DevelopmentError) as captured:
            self.service.load_native(self.identity, "0" * 64)
        self.assertEqual(captured.exception.code, "dll_changed")
        result = self.service.load_native(self.identity, digest)
        self.assertFalse(result["game_ready"])
        self.assertEqual(result["dll_sha256"], digest)
        arguments = self.calls[-1][0]
        self.assertEqual(arguments[arguments.index("-ExpectedDllSha256") + 1], digest)
        self.processes = [{**self.identity, "pid": 999}]
        with self.assertRaises(DevelopmentError) as captured:
            self.service.stop(self.identity)
        self.assertEqual(captured.exception.code, "process_changed")

    def test_bootstrap_stop_accepted_but_load_and_restart_rejected(self):
        bootstrap = {**self.identity, "executable": str(self.service.bootstrap_executable)}
        self.processes = [bootstrap]
        self.responses["stop_g1r_development.ps1"] = CommandResult(0, json.dumps({
            "state": "stopped", "process": bootstrap, "cleanup": [], "outcome_unknown": False}))
        result = self.service.stop(bootstrap)
        self.assertEqual(result["process"], bootstrap)
        self.assertEqual(result["state"], "stop_result")
        self.assertEqual(self.calls[-1][0][self.calls[-1][0].index("-ExpectedPid") + 1], str(bootstrap["pid"]))
        with self.assertRaises(DevelopmentError) as captured:
            self.service.load_native(bootstrap, "0" * 64)
        self.assertEqual(captured.exception.code, "process_path_mismatch")
        self.assertFalse(any("load_native.ps1" in argument for call, _ in self.calls for argument in call))
        recipe = self.recipe()
        with self.assertRaises(DevelopmentError) as captured:
            self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"],
                               restart=True, expected_process=bootstrap)
        self.assertEqual(captured.exception.code, "process_path_mismatch")

    def test_bootstrap_stop_rejects_changed_identity_before_dispatch(self):
        bootstrap = {**self.identity, "executable": str(self.service.bootstrap_executable)}
        self.processes = [bootstrap]
        for wrong in ({**bootstrap, "pid": 999},
                      {**bootstrap, "creation_time": "2026-10-30T01:02:03.0000000Z"},
                      {**bootstrap, "executable": str(self.game / "unrelated.exe")}):
            with self.subTest(expected=wrong), self.assertRaises(DevelopmentError):
                self.service.stop(wrong)
        self.assertFalse(any("stop_g1r_development.ps1" in argument for call, _ in self.calls for argument in call))

    def test_protocol_failure_after_mutation_does_not_repeat_and_marks_unknown(self):
        self.responses["start_g1r_development.ps1"] = CommandResult(0, "not-json")
        recipe = self.recipe()
        with self.assertRaises(DevelopmentError) as captured:
            self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"])
        self.assertTrue(captured.exception.outcome_unknown)
        starts = [args for args, _ in self.calls if any("start_g1r_development.ps1" in argument for argument in args)]
        self.assertEqual(len(starts), 1)
        self.responses["start_g1r_development.ps1"] = CommandResult(0, "{}")
        with self.assertRaises(DevelopmentError) as captured:
            self.service.start("Nested/Probe", recipe["manifest"]["manifest_sha256"])
        self.assertTrue(captured.exception.outcome_unknown)

    def test_manifest_rejects_unbounded_and_redirected_package(self):
        with patch("g1r_mcp.development.MAX_FILE_BYTES", 1):
            with self.assertRaises(DevelopmentError):
                self.recipe()
        with patch("g1r_mcp.development.MAX_PACKAGE_DIRECTORIES", 1):
            with self.assertRaises(DevelopmentError) as captured:
                self.recipe()
        self.assertEqual(captured.exception.code, "manifest_limit")
        external = self.root / "external"
        external.mkdir()
        try:
            (self.package / "foreign").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Система не разрешает isolated symlink")
        with self.assertRaises(DevelopmentError) as captured:
            self.recipe()
        self.assertEqual(captured.exception.code, "unsafe_path")

    def test_manifest_refuses_unreadable_tree_instead_of_partial_hash(self):
        def failing_walk(path, *, followlinks, onerror):
            onerror(PermissionError("isolated denied directory"))
            return iter(())

        with patch("g1r_mcp.development.os.walk", side_effect=failing_walk):
            with self.assertRaises(DevelopmentError) as captured:
                self.recipe()
        self.assertEqual(captured.exception.code, "access_error")

    def test_manifest_ties_validation_to_exact_hashed_descriptor(self):
        from g1r_mcp.packages import validate_package
        original = validate_package
        calls = 0

        def change_after_validation(*arguments):
            nonlocal calls
            calls += 1
            result = original(*arguments)
            if calls == 1:
                (self.package / "GothicMod.json").write_text("invalid-json", encoding="utf-8")
            return result

        with patch("g1r_mcp.development.validate_package", side_effect=change_after_validation):
            with self.assertRaises(DevelopmentError) as captured:
                self.recipe()
        self.assertIn(captured.exception.code, ("package_changed", "invalid_json"))

    def test_manifest_serialized_response_is_bounded(self):
        with patch("g1r_mcp.development.MAX_MANIFEST_BYTES", 1):
            with self.assertRaises(DevelopmentError) as captured:
                self.recipe()
        self.assertEqual(captured.exception.code, "manifest_limit")

    def test_process_state_rejects_unrelated_paths_and_duplicate_json(self):
        self.processes = [{**self.identity, "executable": str(self.root / "other.exe")}]
        with self.assertRaises(DevelopmentError) as captured:
            self.service.process_state()
        self.assertEqual(captured.exception.code, "process_path_mismatch")
        self.processes = [self.identity, self.identity]
        with self.assertRaises(DevelopmentError):
            self.service.process_state()

    def test_real_executor_has_one_deadline_and_no_shell(self):
        from unittest.mock import Mock
        process = Mock(stdout=io.BytesIO(b'{"ok":true}'), stderr=io.BytesIO())
        process.wait.return_value = 0
        with patch("g1r_mcp.development.subprocess.Popen", return_value=process) as run:
            result = execute_command(["fixed", "argument"], PROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.stdout, '{"ok":true}')
        process.wait.assert_called_once_with(timeout=30)
        self.assertNotIn("shell", run.call_args.kwargs)
        process = Mock(stdout=io.BytesIO(), stderr=io.BytesIO())
        process.wait.side_effect = [subprocess.TimeoutExpired(["fixed"], 30), 0]
        with patch("g1r_mcp.development.subprocess.Popen", return_value=process):
            with self.assertRaises(DevelopmentError) as captured:
                execute_command(["fixed"], 30)
        self.assertTrue(captured.exception.outcome_unknown)
        process.kill.assert_called_once()

    def test_executor_caps_pipe_output_while_reading(self):
        from unittest.mock import Mock
        process = Mock(stdout=io.BytesIO(b"x" * 2048), stderr=io.BytesIO())
        process.wait.return_value = 0
        with patch("g1r_mcp.development.MAX_PROCESS_OUTPUT", 100), patch("g1r_mcp.development.subprocess.Popen", return_value=process):
            with self.assertRaises(DevelopmentError) as captured:
                execute_command(["fixed"], 30)
        self.assertEqual(captured.exception.code, "tool_output_too_large")
        self.assertTrue(captured.exception.outcome_unknown)
        process.kill.assert_called_once()

    def test_executor_keyboard_interrupt_settles_helper_without_replay(self):
        from unittest.mock import Mock
        process = Mock(stdout=io.BytesIO(), stderr=io.BytesIO())
        process.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch("g1r_mcp.development.subprocess.Popen", return_value=process) as created:
            with self.assertRaises(KeyboardInterrupt) as captured:
                execute_command(["fixed"], 30)
        created.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(captured.exception.__notes__)


if __name__ == "__main__":
    unittest.main()
