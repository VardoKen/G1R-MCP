"""Отчёт компилятора не заменяет runtime-подтверждение и связан с байтами пакета."""
import json
import tempfile
import unittest
from pathlib import Path
from g1r_mcp.compile_diagnostics import parse_report, read_report
from g1r_mcp.development import DevelopmentService, DevelopmentError


class CompileDiagnosticsTests(unittest.TestCase):
    def test_formats_bind_full_package_suffix_and_do_not_claim_compile_success(self):
        files = [{"path": "Script/Probe.as", "sha256": "a" * 64}]
        report = parse_report('C:\\workspace\\Probe\\Script\\Probe.as (48:1) : Error: Missing override\nScript/Probe.as:12:3: Warning: unused\nOther/Probe.as (4,2) : Error: foreign', "Probe", files)
        self.assertEqual([d["file"] for d in report["diagnostics"]], ["Script/Probe.as", "Script/Probe.as", None])
        self.assertEqual(report["diagnostics"][0]["source_sha256"], "a" * 64)
        self.assertEqual(report["diagnostics"][0]["line"], 48)
        self.assertEqual(report["compilation_state"], "errors_reported")
        self.assertFalse(report["compiled"])
        self.assertFalse(report["report_matches_runtime_compile"])
        empty = parse_report("Everything compiled", "Probe", files)
        self.assertEqual(empty["compilation_state"], "unknown")
        self.assertFalse(empty["compiled"])

    def test_read_report_bounds_paths_hash_and_changed_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); workspace = root / "workspace"; package = workspace / "Probe"
            (package / "Script").mkdir(parents=True)
            for name in ("repo", "modkit", "game"): (root / name).mkdir()
            (package / "GothicMod.json").write_text(json.dumps({"name":"Probe","title":"Probe","author":"Test","modVersion":"1.0.0"}), encoding="utf-8")
            script = package / "Script/Probe.as"; script.write_text("class UProbe {}", encoding="utf-8")
            (workspace / "diagnostic.txt").write_text("Script/Probe.as (3:1) : Error: fixture", encoding="utf-8")
            service = DevelopmentService(repository_root=root / "repo", workspace_root=workspace, modkit_root=root / "modkit", game_root=root / "game")
            manifest = service._manifest("Probe")
            result = read_report(service, "Probe", manifest["manifest_sha256"], "diagnostic.txt")
            self.assertEqual(result["manifest_sha256"], manifest["manifest_sha256"])
            self.assertEqual(result["diagnostics"][0]["source_sha256"], manifest["files"][1]["sha256"])
            for path in ("../diagnostic.txt", str(workspace / "diagnostic.txt"), "Probe/Script/Probe.as"):
                with self.assertRaises(DevelopmentError): read_report(service, "Probe", manifest["manifest_sha256"], path)
            script.write_text("class UChanged {}", encoding="utf-8")
            with self.assertRaises(DevelopmentError) as caught: read_report(service, "Probe", manifest["manifest_sha256"], "diagnostic.txt")
            self.assertEqual(caught.exception.code, "package_changed")

    def test_bounded_report_and_unparsed_lines(self):
        text = "\n".join("Script/A.as (1:1) : Error: item" for _ in range(205))
        report = parse_report(text + "\nFATAL unknown format", "Probe", [{"path":"Script/A.as","sha256":"a" * 64}])
        self.assertEqual(len(report["diagnostics"]), 200)
        self.assertTrue(report["truncated"])
        self.assertEqual(report["unparsed_error_lines"], ["FATAL unknown format"])
