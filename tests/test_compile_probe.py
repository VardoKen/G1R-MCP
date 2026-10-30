"""Изолированная подготовка compiler/reload fixture без игры."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools" / "prepare_compile_probe.py"
SPEC = importlib.util.spec_from_file_location("prepare_compile_probe", TOOL)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
LIVE_SPEC = importlib.util.spec_from_file_location("probe_live_mcp", TOOL.with_name("probe_live_mcp.py"))
import sys
sys.path.insert(0, str(TOOL.parent))
live = importlib.util.module_from_spec(LIVE_SPEC)
LIVE_SPEC.loader.exec_module(live)


class CompileProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name)
        self.workspace = self.repository / "artifacts" / "compile-probe"

    def prepare(self, **arguments):
        return probe.prepare_probe(self.workspace, repository_root=self.repository, **arguments)

    def test_baseline_is_isolated_and_returns_truthful_recipe(self):
        result = self.prepare()
        source = self.workspace / probe.PACKAGE_NAME / probe.SCRIPT_RELATIVE
        self.assertEqual(source.read_bytes(), (probe.FIXTURE_ROOT / probe.SCRIPT_RELATIVE).read_bytes())
        self.assertEqual(result["files"][probe.SCRIPT_RELATIVE]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(result["valid_structure"])
        self.assertEqual(result["script_count"], 1)
        self.assertEqual(result["expected"]["value"], 1)
        self.assertEqual(result["launch_options"], [f"-ModsDirectory={self.workspace.as_posix()}", "-Mods=G1RMCPProbe"])
        self.assertFalse(result["compiled"])
        self.assertFalse(result["reloaded"])
        self.assertFalse(result["game_launched"])
        self.assertFalse(result["dll_loaded"])
        self.assertEqual({path.relative_to(self.workspace / probe.PACKAGE_NAME).as_posix() for path in (self.workspace / probe.PACKAGE_NAME).rglob("*") if path.is_file()}, {"GothicMod.json", probe.SCRIPT_RELATIVE})

    def test_reload_changes_only_constant_and_keeps_descriptor(self):
        baseline = self.prepare()
        descriptor = (self.workspace / probe.PACKAGE_NAME / "GothicMod.json").read_bytes()
        source = self.workspace / probe.PACKAGE_NAME / probe.SCRIPT_RELATIVE
        before = source.read_bytes()
        reload = self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])
        self.assertEqual(source.read_bytes(), before.replace(b"return 1;", b"return 2;", 1))
        self.assertEqual((self.workspace / probe.PACKAGE_NAME / "GothicMod.json").read_bytes(), descriptor)
        self.assertEqual(reload["expected"]["value"], 2)
        self.assertFalse(reload["reloaded"])

    def test_existing_baseline_and_outside_workspace_are_rejected(self):
        self.prepare()
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare()
        self.assertEqual(captured.exception.code, "already_exists")
        for path in (self.repository / "mods", self.repository / "artifacts", self.repository / "artifacts" / ".." / "outside"):
            with self.subTest(path=path), self.assertRaises(probe.PackageError) as captured:
                probe.prepare_probe(path, repository_root=self.repository)
            self.assertEqual(captured.exception.code, "unsafe_path")

    def test_reload_refuses_changed_source_or_descriptor_without_writing(self):
        baseline = self.prepare()
        source = self.workspace / probe.PACKAGE_NAME / probe.SCRIPT_RELATIVE
        source.write_bytes(source.read_bytes() + b"// User edit\n")
        before = source.read_bytes()
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])
        self.assertEqual(captured.exception.code, "file_changed")
        self.assertEqual(source.read_bytes(), before)

    def test_reload_requires_matching_hash_and_baseline(self):
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare(phase="reload")
        self.assertEqual(captured.exception.code, "invalid_hash")
        baseline = self.prepare()
        before = (self.workspace / probe.PACKAGE_NAME / probe.SCRIPT_RELATIVE).read_bytes()
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare(phase="reload", expected_sha256="0" * 64)
        self.assertEqual(captured.exception.code, "file_changed")
        self.assertEqual((self.workspace / probe.PACKAGE_NAME / probe.SCRIPT_RELATIVE).read_bytes(), before)
        self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])
        with self.assertRaises(probe.PackageError):
            self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])

    def test_reload_refuses_descriptor_or_extra_files(self):
        baseline = self.prepare()
        descriptor = self.workspace / probe.PACKAGE_NAME / "GothicMod.json"
        original = descriptor.read_bytes()
        descriptor.write_bytes(original.replace(b'"1.0.0"', b'"1.0.1"'))
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])
        self.assertEqual(captured.exception.code, "fixture_changed")
        descriptor.write_bytes(original)
        extra = self.workspace / probe.PACKAGE_NAME / "extra.txt"
        extra.write_text("User data", encoding="utf-8")
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare(phase="reload", expected_sha256=baseline["files"][probe.SCRIPT_RELATIVE]["sha256"])
        self.assertEqual(captured.exception.code, "fixture_changed")
        self.assertEqual(extra.read_text(encoding="utf-8"), "User data")

    def test_symlink_workspace_is_rejected(self):
        target = self.repository / "outside"
        target.mkdir()
        (self.repository / "artifacts").mkdir()
        try:
            self.workspace.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Система не разрешает изолированный symlink")
        with self.assertRaises(probe.PackageError) as captured:
            self.prepare()
        self.assertEqual(captured.exception.code, "unsafe_path")
        self.assertEqual(list(target.iterdir()), [])

    def test_failed_copy_leaves_no_partial_package_or_stage(self):
        original = Path.write_bytes

        def fail_script(path, data):
            if path.name == "G1RMCPProbe.as" and ".g1r-mcp-probe-" in str(path):
                raise OSError("Isolated write failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", fail_script):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_live_probe_checks_signature_before_invocation(self):
        valid = {"name": "ReadProbeValue", "parameter_size": 4,
                 "parameters": [{"return": True, "type": "IntProperty", "element_size": 4,
                                 "reference": False, "out": True}]}
        self.assertEqual(live.check_signature({"items": [valid]}), valid)
        for parameters in ([], [{"return": False, "type": "IntProperty", "element_size": 4}],
                           [{"return": True, "type": "StrProperty", "element_size": 16}]):
            with self.subTest(parameters=parameters), self.assertRaises(live.ProbeError):
                live.check_signature({"items": [{**valid, "parameters": parameters}]})
        with self.assertRaises(live.ProbeError):
            live.check_signature({"items": [valid, valid]})


if __name__ == "__main__":
    unittest.main()
