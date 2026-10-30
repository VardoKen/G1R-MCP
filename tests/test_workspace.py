"""Изолированные проверки рабочего пакета и отказов без игры."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from g1r_mcp.workspace import MAX_CONTENT_BYTES, MAX_EXACT_READ_BYTES, PackageWorkspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = PackageWorkspace(self.root)
        self.metadata = {"name": "Example", "title": "Пример", "author": "Автор", "modVersion": "1.0.0-dev.1"}

    def create(self):
        return self.workspace.create_package("Example", self.metadata)

    def state(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() if path.is_file() else None
                for path in self.root.rglob("*")}

    def assert_error(self, code, call):
        with self.assertRaises(WorkspaceError) as error:
            call()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn(str(self.root), str(error.exception))

    def test_create_package_only_descriptor_and_empty_script(self):
        result = self.create()
        self.assertTrue(result["created"])
        self.assertFalse(result["compiled"])
        self.assertEqual(result["runtime_validation"], "not_performed")
        self.assertTrue(result["validation"]["valid_structure"])
        self.assertEqual(set(self.state()), {"Example", "Example/GothicMod.json", "Example/Script"})
        self.assertEqual(json.loads((self.root / "Example/GothicMod.json").read_text(encoding="utf-8")), self.metadata)
        self.assertEqual(result["sha256"], hashlib.sha256((self.root / "Example/GothicMod.json").read_bytes()).hexdigest())

    def test_creation_conflict_invalid_descriptor_and_missing_parent_leave_no_files(self):
        self.assert_error("invalid_descriptor", lambda: self.workspace.create_package("Broken", {"name": "Missing fields"}))
        self.assertEqual(self.state(), {})
        self.assert_error("access_error", lambda: self.workspace.create_package("missing/Example", self.metadata))
        self.assertEqual(self.state(), {})
        self.create()
        before = self.state()
        self.assert_error("conflict", self.create)
        self.assertEqual(self.state(), before)

    def test_creation_failure_during_stage_cleanup(self):
        original = Path.write_bytes
        def fail_descriptor(path, raw):
            if path.name == "GothicMod.json":
                raise OSError("fixture")
            return original(path, raw)
        with patch.object(Path, "write_bytes", fail_descriptor):
            self.assert_error("access_error", self.create)
        self.assertEqual(self.state(), {})

    def test_create_read_and_compare_and_swap_write(self):
        self.create()
        first = self.workspace.write_package_file("Example", "Script/Example.as", "class UExample {}\n// привет\n")
        self.assertTrue(first["created"])
        read = self.workspace.read_package_file("Example", "Script/Example.as", 2, 1)
        self.assertEqual(read["text"], "// привет")
        self.assertEqual(read["sha256"], first["sha256"])
        self.assertEqual(read["total_lines"], 2)
        replacement = self.workspace.write_package_file("Example", "Script/Example.as", "// заменено\n", read["sha256"])
        self.assertFalse(replacement["created"])
        self.assertNotEqual(replacement["sha256"], first["sha256"])
        self.assertEqual(self.workspace.read_package_file("Example", "Script/Example.as")["text"], "// заменено")

    def test_patch_first_write_creates_directory_and_preserves_unified_diff_text(self):
        self.create()
        patch = "diff --git a/Script/Test.as b/Script/Test.as\n--- a/Script/Test.as\n+++ b/Script/Test.as\n@@ -1 +1 @@\n-old\n+new\n"
        result = self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", patch)
        self.assertTrue(result["created"])
        self.assertTrue((self.root / "Example/ScriptPatches").is_dir())
        read = self.workspace.read_package_file("Example", "ScriptPatches/Changes.patch")
        self.assertEqual(read["text"], patch.rstrip("\n"))
        self.assertEqual(read["sha256"], result["sha256"])
        self.assertFalse(read["compiled"])
        self.assertEqual(read["runtime_validation"], "not_performed")
        replacement = self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", patch + "\n", read["sha256"])
        self.assertFalse(replacement["created"])
        self.assertEqual([item["path"] for item in self.workspace.list_package_files("Example")["items"]],
                         ["GothicMod.json", "ScriptPatches/Changes.patch"])
        before = self.state()
        self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "overwrite", read["sha256"]))
        self.assertEqual(before, self.state())

    def test_patch_allowlist_refusals_leave_no_directory(self):
        self.create()
        before = self.state()
        for path in ("ScriptPatches/Changes.json", "ScriptPatches/Changes.diff", "ScriptPatches/nested/Changes.patch",
                     "ScriptPatches/main.as", "ScriptPatches"):
            self.assert_error("unsupported_file", lambda: self.workspace.write_package_file("Example", path, "new"))
        self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "new", "0" * 64))
        self.assert_error("limit_exceeded", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "a" * (MAX_CONTENT_BYTES + 1)))
        self.assertEqual(before, self.state())

    def test_new_patch_directory_is_cleaned_on_write_failure(self):
        self.create()
        before = self.state()
        with patch("g1r_mcp.workspace.os.link", side_effect=OSError("fixture")):
            self.assert_error("access_error", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "fixture"))
        self.assertEqual(before, self.state())

    def test_existing_patch_directory_and_unrelated_files_survive_write_failure(self):
        self.create()
        directory = self.root / "Example/ScriptPatches"
        directory.mkdir()
        (directory / "Notes.txt").write_text("preserve", encoding="utf-8")
        before = self.state()
        with patch("g1r_mcp.workspace.os.link", side_effect=OSError("fixture")):
            self.assert_error("access_error", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "fixture"))
        self.assertEqual(before, self.state())

    def test_unrelated_file_created_in_new_patch_directory_is_preserved_on_failure(self):
        self.create()
        directory = self.root / "Example/ScriptPatches"
        def foreign_file_then_failure(*args, **kwargs):
            (directory / "User.txt").write_text("preserve", encoding="utf-8")
            raise OSError("fixture")
        with patch("g1r_mcp.workspace.os.link", side_effect=foreign_file_then_failure):
            self.assert_error("access_error", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "fixture"))
        self.assertEqual((directory / "User.txt").read_text(encoding="utf-8"), "preserve")
        self.assertEqual([path.name for path in directory.iterdir()], ["User.txt"])

    def test_patch_protected_source_boundary_and_utf8_limit(self):
        self.create()
        protected = self.root / "Example/ScriptPatches"
        protected.mkdir()
        workspace = PackageWorkspace(self.root, protected_roots=[protected])
        before = self.state()
        self.assert_error("unsafe_path", lambda: workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "new"))
        self.assertEqual(before, self.state())
        file = protected / "Invalid.patch"
        file.write_bytes(b"\xff")
        self.assert_error("invalid_encoding", lambda: self.workspace.read_package_file("Example", "ScriptPatches/Invalid.patch"))

    def test_script_patch_directory_symlink_refused(self):
        self.create()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            link = self.root / "Example/ScriptPatches"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("Система не разрешает изолированный symlink")
            try:
                self.assert_error("unsafe_path", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "new"))
                self.assertFalse((external / "Changes.patch").exists())
            finally:
                link.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_script_patch_junction_refused(self):
        self.create()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            link = self.root / "Example/ScriptPatches"
            operation = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(external)], capture_output=True)
            if operation.returncode:
                self.skipTest("Система не разрешает изолированный junction")
            try:
                self.assert_error("unsafe_path", lambda: self.workspace.write_package_file("Example", "ScriptPatches/Changes.patch", "new"))
                self.assertFalse((external / "Changes.patch").exists())
            finally:
                link.rmdir()

    def test_absent_existing_and_stale_hash_conflicts_preserve_contents(self):
        self.create()
        self.workspace.write_package_file("Example", "Script/Example.as", "original")
        before = self.state()
        self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "Script/Example.as", "overwrite"))
        self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "Script/Example.as", "overwrite", "0" * 64))
        self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "Script/Missing.as", "create", "0" * 64))
        self.assertEqual(self.state(), before)

    def test_two_agents_with_same_hash_only_one_succeeds(self):
        self.create()
        first = self.workspace.write_package_file("Example", "Script/Example.as", "original")
        results = []
        def writer(text):
            try:
                self.workspace.write_package_file("Example", "Script/Example.as", text, first["sha256"])
                results.append("success")
            except WorkspaceError as error:
                results.append(error.code)
        threads = [threading.Thread(target=writer, args=(text,)) for text in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["success", "conflict"])

    def test_independent_workspace_instances_share_destination_lock(self):
        self.create()
        first = self.workspace.write_package_file("Example", "Script/Example.as", "original")
        second_workspace = PackageWorkspace(self.root)
        barrier = threading.Barrier(2)
        results = []
        def writer(workspace, text):
            barrier.wait()
            try:
                workspace.write_package_file("Example", "Script/Example.as", text, first["sha256"])
                results.append("success")
            except WorkspaceError as error:
                results.append(error.code)
        threads = [threading.Thread(target=writer, args=(workspace, text))
                   for workspace, text in ((self.workspace, "first"), (second_workspace, "second"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["success", "conflict"])

    def test_change_between_preflight_and_replace_is_rejected(self):
        self.create()
        first = self.workspace.write_package_file("Example", "Script/Example.as", "original")
        target = self.root / "Example/Script/Example.as"
        original = os.fsync
        def mutate(fd):
            original(fd)
            target.write_text("user change", encoding="utf-8")
        with patch("g1r_mcp.workspace.os.fsync", side_effect=mutate):
            self.assert_error("conflict", lambda: self.workspace.write_package_file("Example", "Script/Example.as", "overwrite", first["sha256"]))
        self.assertEqual(target.read_text(encoding="utf-8"), "user change")
        self.assertFalse(any(path.name.startswith(".g1r-mcp-") for path in self.root.rglob("*")))

    def test_invalid_descriptor_write_preserves_original(self):
        self.create()
        read = self.workspace.read_package_file("Example", "GothicMod.json")
        before = self.state()
        for content in ('{"name":"bad"}', '{"name":"a","name":"b"}', "[]", "not json"):
            self.assert_error("invalid_descriptor", lambda: self.workspace.write_package_file("Example", "GothicMod.json", content, read["sha256"]))
            self.assertEqual(self.state(), before)
        changed = dict(self.metadata, title="Новое название")
        result = self.workspace.write_package_file("Example", "GothicMod.json", json.dumps(changed), read["sha256"])
        self.assertTrue(result["validation"]["valid_structure"])

    def test_paths_reject_windows_devices_parent_absolute_and_alternate_streams(self):
        self.create()
        before = self.state()
        unsafe = ("../Example", "C:/outside", "/outside", "//server/share", "Example/../Example",
                  "Example:private", "Example.", "Example ", "NUL", "COM1.as", "Example\\child", "Example/", "Example/./child")
        for path in unsafe:
            with self.subTest(path=path):
                self.assert_error("unsafe_path", lambda: self.workspace.create_package(path, self.metadata))
        for path in ("Script/../private.as", "Script/CON.as", "Script/A.as:private", "/Script/A.as", "Script/A.as."):
            with self.subTest(path=path):
                self.assert_error("unsafe_path", lambda: self.workspace.write_package_file("Example", path, "unsafe"))
        self.assertEqual(self.state(), before)

    def test_allowlist_and_missing_script_parent_do_not_modify_other_files(self):
        self.create()
        content = self.root / "Example/Content"
        content.mkdir()
        (content / "private.txt").write_text("private", encoding="utf-8")
        before = self.state()
        for path in ("README.md", "Config/settings.ini", "Content/private.txt", "Scripts/main.lua", "Script/not_source.txt"):
            self.assert_error("unsupported_file", lambda: self.workspace.read_package_file("Example", path))
            self.assert_error("unsupported_file", lambda: self.workspace.write_package_file("Example", path, "overwrite"))
        self.assert_error("access_error", lambda: self.workspace.write_package_file("Example", "Script/new/A.as", "new"))
        self.assertEqual(self.state(), before)
        listed = self.workspace.list_package_files("Example")
        self.assertEqual(listed["items"], [{"path": "GothicMod.json", "bytes": (self.root / "Example/GothicMod.json").stat().st_size}])
        self.assertNotIn("private", repr(listed))

    def test_list_order_limit_and_workspace_root_package(self):
        self.create()
        for name in ("z", "a", "m"):
            self.workspace.write_package_file("Example", f"Script/{name}.as", name)
        result = self.workspace.list_package_files("Example", 2)
        self.assertEqual([item["path"] for item in result["items"]], ["GothicMod.json", "Script/a.as"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total"], 4)
        root_workspace = PackageWorkspace(self.root / "Example")
        self.assertEqual(root_workspace.read_package_file(".", "Script/a.as")["package_path"], ".")

    def test_encoding_limits_and_read_truncation(self):
        self.create()
        before = self.state()
        for content in ("a\0b", "\ud800"):
            code = "invalid_arguments" if "\0" in content else "invalid_encoding"
            self.assert_error(code, lambda: self.workspace.write_package_file("Example", "Script/A.as", content))
        self.assert_error("limit_exceeded", lambda: self.workspace.write_package_file("Example", "Script/A.as", "a" * (MAX_CONTENT_BYTES + 1)))
        self.assertEqual(before, self.state())
        target = self.root / "Example/Script/A.as"
        target.write_bytes(b"\xff")
        self.assert_error("invalid_encoding", lambda: self.workspace.read_package_file("Example", "Script/A.as"))
        target.write_text("я" * 50_000, encoding="utf-8")
        result = self.workspace.read_package_file("Example", "Script/A.as")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode("utf-8")), 64 * 1024)
        self.assertEqual(result["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())

    def test_exact_text_round_trip_preserves_line_endings_bom_and_eof(self):
        self.create()
        cases = (b"line\nnext\n", b"line\r\nnext\r\n", b"line\r\nnext\nend",
                 b"\xef\xbb\xbfline\r\nnext", b"no final newline", b"")
        for file_path in ("Script/Exact.as", "ScriptPatches/Exact.patch"):
            previous = None
            for raw in cases:
                with self.subTest(file_path=file_path, raw=raw):
                    written = self.workspace.write_package_file("Example", file_path, raw.decode("utf-8"), previous)
                    exact = self.workspace.read_package_file_exact("Example", file_path)
                    self.assertEqual(exact["text"].encode("utf-8"), raw)
                    self.assertEqual(exact["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertEqual(exact["sha256"], written["sha256"])
                    self.assertEqual(exact["bytes"], len(raw))
                    self.assertEqual(exact["text_mode"], "exact")
                    self.assertFalse(exact["truncated"])
                    self.assertFalse(exact["compiled"])
                    unchanged = self.workspace.write_package_file("Example", file_path, exact["text"], exact["sha256"])
                    self.assertEqual(unchanged["sha256"], exact["sha256"])
                    previous = unchanged["sha256"]
        descriptor = self.root / "Example/GothicMod.json"
        raw = b"\xef\xbb\xbf" + descriptor.read_bytes().replace(b"\n", b"\r\n")
        descriptor.write_bytes(raw)
        exact = self.workspace.read_package_file_exact("Example", "GothicMod.json")
        self.assertEqual(exact["text"].encode("utf-8"), raw)
        self.workspace.write_package_file("Example", "GothicMod.json", exact["text"], exact["sha256"])
        self.assertEqual(descriptor.read_bytes(), raw)

    def test_exact_text_size_limit_is_explicit_and_never_truncates(self):
        self.create()
        file = self.root / "Example/Script/Exact.as"
        raw = "я" * (MAX_EXACT_READ_BYTES // 2)
        file.write_bytes(raw.encode("utf-8"))
        exact = self.workspace.read_package_file_exact("Example", "Script/Exact.as")
        self.assertEqual(exact["bytes"], MAX_EXACT_READ_BYTES)
        self.assertEqual(exact["text"], raw)
        self.assertFalse(exact["truncated"])
        file.write_bytes(b"a" * (MAX_EXACT_READ_BYTES + 1))
        self.assert_error("file_too_large", lambda: self.workspace.read_package_file_exact("Example", "Script/Exact.as"))
        descriptor = self.root / "Example/GothicMod.json"
        descriptor.write_bytes(b"a" * (256 * 1024 + 1))
        self.assert_error("file_too_large", lambda: self.workspace.read_package_file_exact("Example", "GothicMod.json"))

    def test_exact_read_uses_allowlist_protected_roots_and_utf8(self):
        self.create()
        self.assert_error("unsupported_file", lambda: self.workspace.read_package_file_exact("Example", "Content/private.txt"))
        self.assert_error("unsafe_path", lambda: self.workspace.read_package_file_exact("Example", "Script/../private.as"))
        protected = PackageWorkspace(self.root, protected_roots=[self.root / "Example"])
        self.assert_error("unsafe_path", lambda: protected.read_package_file_exact("Example", "GothicMod.json"))
        invalid = self.root / "Example/Script/Invalid.as"
        invalid.write_bytes(b"\xff")
        self.assert_error("invalid_encoding", lambda: self.workspace.read_package_file_exact("Example", "Script/Invalid.as"))

    def test_exact_read_refuses_symlink_without_returning_external_content(self):
        self.create()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "Private.as"
            external.write_text("private", encoding="utf-8")
            link = self.root / "Example/Script/Linked.as"
            try:
                link.symlink_to(external)
            except OSError:
                self.skipTest("Система не разрешает изолированный symlink")
            try:
                self.assert_error("unsafe_path", lambda: self.workspace.read_package_file_exact("Example", "Script/Linked.as"))
            finally:
                link.unlink()

    def test_paged_read_explicitly_identifies_display_text(self):
        self.create()
        raw = b"\xef\xbb\xbfline\r\nnext"
        self.workspace.write_package_file("Example", "Script/Display.as", raw.decode("utf-8"))
        display = self.workspace.read_package_file("Example", "Script/Display.as")
        self.assertEqual(display["text_mode"], "display_lines")
        self.assertEqual(display["text"], "line\nnext")
        self.assertEqual(display["sha256"], hashlib.sha256(raw).hexdigest())

    def test_invalid_arguments(self):
        self.create()
        for value in (0, 201, True, 1.5):
            self.assert_error("invalid_arguments", lambda: self.workspace.list_package_files("Example", value))
        for start, count in ((0, 1), (True, 1), (1, 401), (1, True)):
            self.assert_error("invalid_arguments", lambda: self.workspace.read_package_file("Example", "GothicMod.json", start, count))
        self.assert_error("invalid_arguments", lambda: self.workspace.write_package_file("Example", "GothicMod.json", "{}", "bad-hash"))

    def test_protected_root_equal_workspace_blocks_all_package_operations(self):
        self.create()
        before = self.state()
        workspace = PackageWorkspace(self.root, protected_roots=[self.root])
        for call in (
            lambda: workspace.create_package("Other", self.metadata),
            lambda: workspace.list_package_files("Example"),
            lambda: workspace.read_package_file("Example", "GothicMod.json"),
            lambda: workspace.write_package_file("Example", "Script/New.as", "new"),
        ):
            self.assert_error("unsafe_path", call)
        self.assertEqual(self.state(), before)

    def test_workspace_ancestor_of_modkit_allows_sibling_packages(self):
        modkit = self.root / "Modkit"
        source = modkit / "Script/Official.as"
        source.parent.mkdir(parents=True)
        source.write_text("class UOfficial {}", encoding="utf-8")
        (modkit / "GothicMod.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        protected = PackageWorkspace(self.root, protected_roots=[modkit / "Script/.."])
        self.assertTrue(protected.create_package("Sibling", self.metadata)["created"])
        protected.write_package_file("Sibling", "Script/Sibling.as", "sibling")
        self.assertEqual(protected.read_package_file("Sibling", "Script/Sibling.as")["text"], "sibling")
        self.assertEqual(protected.list_package_files("Sibling")["total"], 2)
        before = self.state()
        for call in (
            lambda: protected.create_package("Modkit/New", self.metadata),
            lambda: protected.list_package_files("Modkit"),
            lambda: protected.read_package_file("Modkit", "Script/Official.as"),
            lambda: protected.read_package_file("Modkit", "GothicMod.json"),
            lambda: protected.write_package_file("Modkit", "Script/New.as", "overwrite"),
            lambda: protected.create_package("Modkit", self.metadata),
        ):
            self.assert_error("unsafe_path", call)
        self.assertEqual(self.state(), before)
        self.assertEqual(source.read_text(encoding="utf-8"), "class UOfficial {}")

    def test_protected_nested_target_cannot_be_read_or_written_through_ancestor_package(self):
        self.create()
        protected = self.root / "Example/Script/Official"
        protected.mkdir()
        source = protected / "Official.as"
        source.write_text("protected", encoding="utf-8")
        workspace = PackageWorkspace(self.root, protected_roots=[protected])
        before = self.state()
        for call in (
            lambda: workspace.list_package_files("Example"),
            lambda: workspace.read_package_file("Example", "Script/Official/Official.as"),
            lambda: workspace.write_package_file("Example", "Script/Official/New.as", "overwrite"),
            lambda: workspace.create_package("Example", self.metadata),
        ):
            self.assert_error("unsafe_path", call)
        self.assertEqual(self.state(), before)

    def test_protected_root_parent_of_workspace_blocks_child_work(self):
        child = self.root / "Child"
        child.mkdir()
        workspace = PackageWorkspace(child, protected_roots=[self.root])
        self.assert_error("unsafe_path", lambda: workspace.create_package("New", self.metadata))
        self.assertEqual(list(child.iterdir()), [])

    def test_symlink_workspace_file_and_directory_are_rejected(self):
        self.create()
        external = self.root / "external"
        external.mkdir()
        (external / "A.as").write_text("private", encoding="utf-8")
        link = self.root / "Example/Script/A.as"
        try:
            link.symlink_to(external / "A.as")
        except OSError:
            self.skipTest("Система не разрешает изолированный symlink")
        self.assert_error("unsafe_path", lambda: self.workspace.read_package_file("Example", "Script/A.as"))
        self.assert_error("unsafe_path", lambda: self.workspace.list_package_files("Example"))
        link.unlink()
        link.symlink_to(external, target_is_directory=True)
        self.assert_error("unsafe_path", lambda: self.workspace.write_package_file("Example", "Script/A.as/new.as", "new"))
        self.assert_error("unsafe_path", lambda: PackageWorkspace(link))

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_windows_junction_is_rejected_without_reading_target(self):
        self.create()
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)
            (target / "Private.as").write_text("private", encoding="utf-8")
            junction = self.root / "Example/Script/linked"
            command = ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)]
            operation = subprocess.run(command, capture_output=True, text=True)
            if operation.returncode:
                self.skipTest("Система не разрешает изолированный junction")
            try:
                self.assert_error("unsafe_path", lambda: self.workspace.read_package_file("Example", "Script/linked/Private.as"))
                self.assert_error("unsafe_path", lambda: self.workspace.write_package_file("Example", "Script/linked/New.as", "new"))
                self.assert_error("unsafe_path", lambda: self.workspace.list_package_files("Example"))
                self.assertEqual((target / "Private.as").read_text(encoding="utf-8"), "private")
                self.assertFalse((target / "New.as").exists())
            finally:
                # Удаляется только сама ссылка в известной временной рабочей базе.
                junction.rmdir()


if __name__ == "__main__":
    unittest.main()
