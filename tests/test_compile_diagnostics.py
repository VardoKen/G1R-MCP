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

    def test_all_lossy_limits_set_truncated(self):
        files = [{"path": "Script/Probe.as", "sha256": "a" * 64}]
        message = parse_report("Script/Probe.as (1:1) : Error: " + "x" * 5000, "Probe", files)
        self.assertTrue(message["truncated"])
        self.assertTrue(message["diagnostics"][0]["message_truncated"])
        self.assertEqual(len(message["diagnostics"][0]["message"]), 4096)
        self.assertFalse(message["unparsed_truncated"])
        many = parse_report("\n".join("Error: item " + str(i) for i in range(25)), "Probe", files)
        self.assertTrue(many["truncated"])
        self.assertTrue(many["unparsed_truncated"])
        self.assertEqual(len(many["unparsed_error_lines"]), 20)
        long_raw = parse_report("FATAL " + "x" * 3000, "Probe", files)
        self.assertTrue(long_raw["truncated"])
        self.assertTrue(long_raw["unparsed_truncated"])
        self.assertEqual(len(long_raw["unparsed_error_lines"][0]), 2048)
        exact = parse_report("Script/Probe.as (1:1) : Error: " + "x" * 4096, "Probe", files)
        self.assertFalse(exact["truncated"])
        self.assertFalse(exact["diagnostics"][0]["message_truncated"])
        self.assertFalse(exact["compiled"])
        self.assertFalse(exact["report_matches_runtime_compile"])

    def test_quoted_windows_and_unicode_paths_bind_without_exposing_absolute_path(self):
        files = [{"path": "Script/Проба.as", "sha256": "a" * 64}]
        text = '"C:\\workspace\\Probe\\Script\\Проба.as" (48:1) : Error: missing\n\'Script/Проба.as\':12:3: Warning: unused'
        report = parse_report(text, "Probe", files)
        self.assertEqual([item["file"] for item in report["diagnostics"]], ["Script/Проба.as", "Script/Проба.as"])
        self.assertEqual([item["severity"] for item in report["diagnostics"]], ["error", "warning"])
        self.assertEqual([item["line"] for item in report["diagnostics"]], [48, 12])
        self.assertTrue(all(item["source_sha256"] == "a" * 64 for item in report["diagnostics"]))
        self.assertFalse(report["truncated"])
        self.assertFalse(report["report_matches_runtime_compile"])
        apostrophe = parse_report("Script/O'Neil.as (1:1) : Error: legal filename", "Probe", [{"path": "Script/O'Neil.as", "sha256": "b" * 64}])
        self.assertEqual(apostrophe["diagnostics"][0]["file"], "Script/O'Neil.as")

    def test_zero_coordinates_are_preserved_as_unparsed_unknown(self):
        files = [{"path": "Script/Probe.as", "sha256": "a" * 64}]
        text = "Script/Probe.as (0:1) : Error: zero line\nScript/Probe.as:1:0: Error: zero column\nScript/Probe.as:2: Warning: valid absent column"
        report = parse_report(text, "Probe", files)
        self.assertEqual(len(report["diagnostics"]), 1)
        self.assertEqual(report["diagnostics"][0]["line"], 2)
        self.assertIsNone(report["diagnostics"][0]["column"])
        self.assertEqual(len(report["unparsed_error_lines"]), 2)
        self.assertEqual(report["compilation_state"], "unknown")
        self.assertFalse(report["compiled"])

    def test_unparseable_huge_coordinate_does_not_abort_following_diagnostic(self):
        files = [{"path": "Script/Probe.as", "sha256": "a" * 64}]
        text = "Script/Probe.as (" + "9" * 5000 + ":1) : Error: malformed\nScript/Probe.as (2:3) : Error: valid"
        report = parse_report(text, "Probe", files)
        self.assertEqual(len(report["diagnostics"]), 1)
        self.assertEqual(report["diagnostics"][0]["line"], 2)
        self.assertEqual(report["diagnostics"][0]["column"], 3)
        self.assertEqual(len(report["unparsed_error_lines"]), 1)
        self.assertTrue(report["unparsed_truncated"])
        self.assertTrue(report["truncated"])
        self.assertFalse(report["compiled"])
