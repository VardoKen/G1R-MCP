"""Изолированные проверки FName-fixture и её безопасного MCP-прохода."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("prepare_name_probe")
live = load("probe_name_mcp")
class_prepare = load("prepare_class_probe")


def functions():
    return {"items": [{"name": method, "parameter_size": size,
                      "parameters": [{"name": "ReturnValue", "type": field_type, "element_size": size,
                                      "offset": 0, "return": True, "out": True, "reference": False}]}
                     for method, (field_type, size) in live.METHODS.items()]}


class NameCallFixture:
    def __init__(self, *, wrong_return=False, false_change=False, wrong_owner=False, cleanup_unconfirmed=False):
        self.target = {"handle": "obj-1", "class": live.HOST, "name": "Default__" + live.HOST,
                       "path": "/Script/Angelscript.Default__" + live.HOST}
        self.calls = []
        self.wrong_return = wrong_return
        self.false_change = false_change
        self.wrong_owner = wrong_owner
        self.cleanup_unconfirmed = cleanup_unconfirmed
        self.active = False

    def snapshot(self):
        return {"session_id": "other-owner" if self.wrong_owner else "fixture-owner",
                "source": "live_native", "consistency": "single_tick_sequential",
                "partial": False, "truncated": False, "objects": [{**self.target,
                "properties": [{"name": name, "type": "NameProperty", "supported": True, "value": value}
                               for name, value in live.PROPERTIES.items()]}]}

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "g1r_native_status":
            return {"ready": True, "session_id": "fixture-owner"}
        if name == "g1r_find_objects":
            return {"items": [self.target]}
        if name == "g1r_object_functions":
            return functions()
        if name == "g1r_invoke":
            method = arguments["function"]
            if method == "ReadMarker":
                return {"executed": True, "result": 71}
            if method in ("ReadText", "ReadEmptyText"):
                return {"executed": True, "result": "G1R MCP dev7" if method == "ReadText" else "", "cleanup_confirmed": not self.cleanup_unconfirmed}
            return {"executed": True, "result": "Wrong" if self.wrong_return else "Entry" if method == "ReadEntry" else "None"}
        if name == "g1r_snapshot":
            return self.snapshot()
        if name == "g1r_watch_start":
            self.active = True
            return {"watch_id": "watch-1"}
        if name == "g1r_watch_read":
            events = [{"kind": "baseline", "snapshot": self.snapshot()}]
            if self.false_change:
                events.append({"kind": "changed", "snapshot": self.snapshot()})
            return {"session_id": "fixture-owner", "state": "running", "events": events, "gap": False, "dropped_events": 0}
        if name == "g1r_watch_stop":
            self.active = False
            return {"cleanup_complete": True, "state": "stopped"}
        if name == "g1r_watch_forget":
            return {"cleanup_complete": True, "forgotten": True}
        raise AssertionError((name, arguments))


class NameProbeTests(unittest.TestCase):
    def test_prepare_copies_own_source_and_keeps_runtime_unverified(self):
        original_class_constants = (class_prepare.PACKAGE_NAME, class_prepare.SCRIPT_RELATIVE, class_prepare.FIXTURE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "artifacts").mkdir()
            workspace = repository / "artifacts" / "name-probe"
            result = prepare.prepare_probe(workspace, repository_root=repository)
            self.assertEqual((workspace / prepare.PACKAGE_NAME / prepare.SCRIPT_RELATIVE).read_bytes(),
                             (prepare.FIXTURE_ROOT / prepare.SCRIPT_RELATIVE).read_bytes())
            self.assertEqual(result["package_name"], prepare.PACKAGE_NAME)
            self.assertEqual(result["expected"]["marker"], 71)
            self.assertFalse(result["compiled"])
            self.assertFalse(result["game_launched"])
            self.assertEqual(list(workspace.iterdir()), [workspace / prepare.PACKAGE_NAME])
        self.assertEqual((class_prepare.PACKAGE_NAME, class_prepare.SCRIPT_RELATIVE, class_prepare.FIXTURE_ROOT), original_class_constants)

    def test_reused_preparation_cleans_after_write_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "artifacts").mkdir()
            workspace = repository / "artifacts" / "name-probe"
            with patch.object(class_prepare.PackageWorkspace, "write_package_file", side_effect=prepare.WorkspaceError("isolated_failure", "write failure")):
                with self.assertRaises(prepare.WorkspaceError):
                    prepare.prepare_probe(workspace, repository_root=repository)
            self.assertEqual(list(workspace.iterdir()), [])

    def test_existing_and_outside_destinations_are_not_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "artifacts").mkdir()
            workspace = repository / "artifacts" / "name-probe"
            prepare.prepare_probe(workspace, repository_root=repository)
            source = workspace / prepare.PACKAGE_NAME / prepare.SCRIPT_RELATIVE
            original = source.read_bytes()
            with self.assertRaises(prepare.PackageError) as captured:
                prepare.prepare_probe(workspace, repository_root=repository)
            self.assertEqual(captured.exception.code, "already_exists")
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(prepare.PackageError) as captured:
                prepare.prepare_probe(repository / "outside", repository_root=repository)
            self.assertEqual(captured.exception.code, "unsafe_path")

    def test_signatures_require_name_return_eight_bytes_and_no_input(self):
        declaration = functions()
        for method in live.METHODS:
            live.check_signature(declaration, method)
        declaration["items"][1]["parameters"][0]["type"] = "StrProperty"
        with self.assertRaises(live.ProbeError):
            live.check_signature(declaration, "ReadEntry")
        declaration = functions()
        declaration["items"][2]["parameters"].append({"name": "Input", "return": False, "type": "NameProperty", "element_size": 8})
        with self.assertRaises(live.ProbeError):
            live.check_signature(declaration, "ReadNone")

    def test_live_pass_repeats_returns_and_cleans_observation(self):
        fixture = NameCallFixture()
        report = asyncio.run(live.exercise(fixture.call))
        self.assertEqual(report["returns"], {"ReadEntry": {"text": "Entry", "repetitions": 3},
                                            "ReadNone": {"text": "None", "repetitions": 3},
                                            "ReadText": {"text": "G1R MCP dev7", "repetitions": 3},
                                            "ReadEmptyText": {"text": "", "repetitions": 3}})
        self.assertEqual(report["cleanup"], ["g1r_watch_stop", "g1r_watch_forget"])
        self.assertFalse(fixture.active)

    def test_wrong_return_or_snapshot_owner_rejects_without_watch(self):
        for fixture in (NameCallFixture(wrong_return=True), NameCallFixture(wrong_owner=True)):
            with self.subTest(fixture=fixture), self.assertRaises(live.ProbeError):
                asyncio.run(live.exercise(fixture.call))
            self.assertFalse(any(name == "g1r_watch_start" for name, _ in fixture.calls))

    def test_false_change_rejects_but_still_cleans_observation(self):
        fixture = NameCallFixture(false_change=True)
        with self.assertRaises(live.ProbeError) as captured:
            asyncio.run(live.exercise(fixture.call))
        self.assertEqual(captured.exception.code, "probe_false_name_change")
        self.assertFalse(fixture.active)
        self.assertEqual([name for name, _ in fixture.calls[-2:]], ["g1r_watch_stop", "g1r_watch_forget"])

    def test_fstring_cleanup_must_be_explicitly_confirmed(self):
        fixture = NameCallFixture(cleanup_unconfirmed=True)
        with self.assertRaises(live.ProbeError) as captured:
            asyncio.run(live.exercise(fixture.call))
        self.assertEqual(captured.exception.code, "probe_string_cleanup_unconfirmed")
        self.assertFalse(any(name == "g1r_watch_start" for name, _ in fixture.calls))

    def test_cancelled_name_watch_cleanup_still_forgets_observation(self):
        async def scenario():
            fixture = NameCallFixture()
            started, release = asyncio.Event(), asyncio.Event()

            async def call(name, arguments):
                result = await fixture.call(name, arguments)
                if name == "g1r_watch_stop":
                    started.set()
                    await release.wait()
                return result

            task = asyncio.create_task(live.exercise(call))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(fixture.active)
            self.assertEqual([name for name, _ in fixture.calls[-2:]], ["g1r_watch_stop", "g1r_watch_forget"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
