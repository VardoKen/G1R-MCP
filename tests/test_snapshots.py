"""Проверки семантики сравнения выбранных копий и ограничений JSON."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from g1r_mcp.snapshots import MAX_SNAPSHOT_BYTES, SnapshotError, compare_snapshots


def prop(name="Enabled", value=True, kind="BoolProperty"):
    return {"name": name, "type": kind, "supported": True, "value": value}


def snapshot(properties=None, handle="obj-1"):
    return {
        "snapshot_id": "snapshot-1", "source": "live_native", "session_id": "session-1",
        "process_id": 123, "thread_id": 456, "tick_count": 7,
        "consistency": "single_tick_sequential", "truncated": False, "partial": False,
        "objects": [{"handle": handle, "path": "/Script/Probe.Default__Probe", "class": "Probe",
                     "properties": [prop()] if properties is None else properties}],
    }


def object_reference(handle="ref-1", index=12):
    return {"handle": handle, "index": index, "name": "Default__Target", "path": "/Script/Probe.Default__Target",
            "class": "Target", "session_id": "session-1"}


class SnapshotComparisonTests(unittest.TestCase):
    def assert_invalid(self, candidate, code="invalid_snapshot"):
        with self.assertRaises(SnapshotError) as failure:
            compare_snapshots(candidate, snapshot())
        self.assertEqual(failure.exception.code, code)

    def test_equal_values_report_only_selected_copies_and_do_not_mutate(self):
        before = snapshot([prop("A", 1, "IntProperty"), prop("B", "Текст", "StrProperty")])
        after = copy.deepcopy(before)
        after["snapshot_id"] = "snapshot-2"
        after["objects"][0]["properties"].reverse()
        originals = copy.deepcopy((before, after))
        result = compare_snapshots(before, after)
        self.assertEqual((before, after), originals)
        self.assertTrue(result["equal_selected_values"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["after_snapshot_id"], "snapshot-2")
        self.assertEqual(result["source"], "copied_snapshots")
        self.assertEqual(result["provenance"], "unverified")
        self.assertEqual(result["validation"], "client_supplied_copies")

    def test_type_and_value_changes_are_separate_and_bool_is_not_integer(self):
        result = compare_snapshots(snapshot(), snapshot([prop(value=1, kind="IntProperty")]))
        self.assertEqual([item["kind"] for item in result["items"]], ["type_changed", "value_changed"])
        self.assertEqual((result["type_changes"], result["value_changes"]), (1, 1))
        self.assertFalse(result["equal_selected_values"])
        changed_type_only = compare_snapshots(snapshot([prop(value=1, kind="IntProperty")]),
                                              snapshot([prop(value=1, kind="Int64Property")]))
        self.assertEqual(changed_type_only["type_changes"], 1)
        self.assertEqual(changed_type_only["value_changes"], 0)

    def test_different_process_and_session_do_not_report_changes(self):
        for field, reason in (("process_id", "different_process"), ("session_id", "different_session")):
            before, after = snapshot(), snapshot([prop(value=False)])
            after[field] = 789 if field == "process_id" else "session-2"
            result = compare_snapshots(before, after)
            self.assertFalse(result["comparable"])
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["items"], [])
            self.assertIsNone(result["equal_selected_values"])

    def test_changed_target_set_is_coverage_and_not_object_lifecycle(self):
        result = compare_snapshots(snapshot(), snapshot(handle="obj-2"))
        self.assertEqual({item["kind"] for item in result["items"]}, {"coverage_added", "coverage_removed"})
        self.assertEqual(result["coverage_changes"], 2)
        self.assertIsNone(result["equal_selected_values"])
        self.assertEqual(result["value_changes"], 0)

    def test_object_error_and_identity_mismatch_are_unknown(self):
        for code, expected in (("stale_handle", "object_unavailable"),
                               ("object_destroying", "object_unavailable"), ("invalid_memory", "unknown")):
            after = snapshot()
            after["partial"] = True
            after["objects"] = [{"handle": "obj-1", "error": {"code": code, "message": "Недоступен"}}]
            result = compare_snapshots(snapshot(), after)
            self.assertEqual(result["items"][0]["kind"], expected)
            self.assertEqual(result["unknown_count"], 1)
        after = snapshot([prop(value=False)])
        after["objects"][0]["class"] = "Other"
        result = compare_snapshots(snapshot(), after)
        self.assertEqual(result["items"][0]["reason"], "identity_mismatch")
        self.assertEqual(result["value_changes"], 0)

    def test_partial_and_unsupported_do_not_hide_other_comparable_fields(self):
        before = snapshot([prop("A", 1, "IntProperty"), prop("B")])
        after = snapshot([prop("A", 2, "IntProperty"),
                          {"name": "B", "supported": False,
                           "error": {"code": "property_not_found", "message": "Нет свойства"}}])
        after["partial"] = True
        result = compare_snapshots(before, after)
        self.assertEqual((result["value_changes"], result["unknown_count"]), (1, 1))
        self.assertFalse(result["coverage"]["complete"])
        self.assertIsNone(result["equal_selected_values"])
        # Returned errors are separate copies as well.
        result["items"][1]["after_error"]["message"] = "Изменён ответ"
        self.assertEqual(after["objects"][0]["properties"][1]["error"]["message"], "Нет свойства")

    def test_property_absence_is_unknown_coverage_not_deleted_property(self):
        result = compare_snapshots(snapshot([prop("A"), prop("B")]), snapshot([prop("A")]))
        self.assertEqual(result["items"][0]["reason"], "selected_property_coverage_changed")
        self.assertEqual(result["items"][0]["property"], "B")
        self.assertEqual(result["value_changes"], 0)

    def test_missing_source_and_unconfirmed_consistency_block_value_changes(self):
        for mode in ("missing_source", "other_source", "other_consistency"):
            before, after = snapshot(), snapshot([prop(value=False)])
            if mode == "missing_source":
                del before["source"]
            elif mode == "other_source":
                after["source"] = "source_only"
            else:
                after["consistency"] = "unconfirmed"
            result = compare_snapshots(before, after)
            self.assertGreater(result["unknown_count"], 0)
            self.assertEqual(result["value_changes"], 0)
            self.assertIsNone(result["equal_selected_values"])

    def test_truncated_source_cannot_prove_complete_equality(self):
        before = snapshot()
        before["truncated"] = True
        result = compare_snapshots(before, snapshot())
        self.assertTrue(result["coverage"]["before_truncated"])
        self.assertIsNone(result["equal_selected_values"])

    def test_result_limit_preserves_totals(self):
        before = snapshot([prop("A", 1, "IntProperty"), prop("B", 1, "IntProperty")])
        after = snapshot([prop("A", 2, "IntProperty"), prop("B", 2, "IntProperty")])
        result = compare_snapshots(before, after, limit=1)
        self.assertEqual((len(result["items"]), result["total_items"], result["value_changes"]), (1, 2, 2))
        self.assertTrue(result["truncated"])
        for limit in (0, 201, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(SnapshotError) as failure:
                compare_snapshots(before, after, limit)
            self.assertEqual(failure.exception.code, "invalid_limit")

    def test_duplicate_handles_names_and_invalid_shapes_are_rejected(self):
        candidate = snapshot()
        candidate["objects"].append(copy.deepcopy(candidate["objects"][0]))
        self.assert_invalid(candidate)
        self.assert_invalid(snapshot([prop(), prop()]))
        self.assert_invalid(snapshot([{"name": "A", "type": "BoolProperty", "supported": True}]))
        self.assert_invalid(snapshot([prop(value={"nested": 1})]))
        candidate = snapshot()
        candidate["unexpected"] = True
        self.assert_invalid(candidate)
        for field in ("process_id", "thread_id", "tick_count"):
            candidate = snapshot()
            candidate[field] = True
            self.assert_invalid(candidate)
        candidate = snapshot()
        candidate["partial"] = 1
        self.assert_invalid(candidate)

    def test_invalid_utf8_non_finite_and_json_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), "\ud800", b"bytes"):
            with self.subTest(value=repr(value)):
                self.assert_invalid(snapshot([prop(value=value)]))

    def test_object_property_and_serialized_input_bounds(self):
        candidate = snapshot()
        candidate["objects"] = []
        for index in range(16):
            candidate["objects"].append(snapshot(handle=f"obj-{index}")["objects"][0])
        self.assertTrue(compare_snapshots(candidate, candidate)["equal_selected_values"])
        candidate["objects"].append(snapshot(handle="obj-16")["objects"][0])
        self.assert_invalid(candidate)
        candidate = snapshot([prop(f"P{i}") for i in range(32)])
        self.assertTrue(compare_snapshots(candidate, candidate)["equal_selected_values"])
        candidate["objects"][0]["properties"].append(prop("P32"))
        self.assert_invalid(candidate)
        self.assert_invalid(snapshot([prop(value="x" * MAX_SNAPSHOT_BYTES, kind="StrProperty")]),
                            "snapshot_too_large")

    def test_large_diff_respects_byte_bound_without_losing_counts(self):
        before = snapshot([prop(value="a" * 600000, kind="StrProperty")])
        after = snapshot([prop(value="b" * 600000, kind="StrProperty")])
        result = compare_snapshots(before, after)
        self.assertEqual(result["value_changes"], 1)
        self.assertEqual(result["total_items"], 1)
        self.assertEqual(result["items"], [])
        self.assertTrue(result["truncated"])

    def test_actual_native_limits_object_flags_and_null_missing_type(self):
        before, after = snapshot(), snapshot()
        for candidate in (before, after):
            candidate["limits"] = {
                "objects": 16, "properties_per_object": 32, "scan_fields_per_object": 4096,
                "property_value_bytes": 16384, "snapshot_bytes": 262144,
            }
            candidate["objects"][0].update(partial=False, truncated=False)
        self.assertTrue(compare_snapshots(before, after)["equal_selected_values"])
        after["objects"][0]["properties"].append({
            "name": "Missing", "type": None, "supported": False,
            "error": {"code": "property_not_found", "message": "Свойство не найдено"},
        })
        after["objects"][0]["partial"] = True
        after["partial"] = True
        result = compare_snapshots(before, after)
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["coverage"]["after_incomplete_objects"], 1)
        self.assertIsNone(result["equal_selected_values"])
        # Even inconsistent caller-supplied top flags cannot hide an object scan limit.
        after = copy.deepcopy(before)
        after["objects"][0]["truncated"] = True
        result = compare_snapshots(before, after)
        self.assertFalse(result["coverage"]["complete"])
        self.assertIsNone(result["equal_selected_values"])

    def test_native_metadata_validation_remains_strict(self):
        limits = {
            "objects": 16, "properties_per_object": 32, "scan_fields_per_object": 4096,
            "property_value_bytes": 16384, "snapshot_bytes": 262144,
        }
        for key, value in (("objects", True), ("snapshot_bytes", 0), ("other", 1)):
            candidate = snapshot()
            candidate["limits"] = {**limits, key: value}
            self.assert_invalid(candidate)
        candidate = snapshot()
        candidate["limits"] = {"objects": 16}
        self.assert_invalid(candidate)
        for field in ("partial", "truncated"):
            candidate = snapshot()
            candidate["objects"][0][field] = 1
            self.assert_invalid(candidate)
        self.assert_invalid(snapshot([prop(kind=None)]))

    def test_object_reference_and_null_have_stable_declared_type(self):
        null_snapshot = snapshot([prop("Target", None, "ObjectProperty")])
        reference_snapshot = snapshot([prop("Target", object_reference(), "ObjectProperty")])
        self.assertTrue(compare_snapshots(null_snapshot, null_snapshot)["equal_selected_values"])
        self.assertTrue(compare_snapshots(reference_snapshot, reference_snapshot)["equal_selected_values"])
        for before, after in ((null_snapshot, reference_snapshot), (reference_snapshot, null_snapshot)):
            result = compare_snapshots(before, after)
            self.assertEqual(result["type_changes"], 0)
            self.assertEqual(result["value_changes"], 1)
            self.assertEqual(result["items"][0]["kind"], "value_changed")
        other = snapshot([prop("Target", object_reference("ref-2", 13), "ObjectProperty")])
        result = compare_snapshots(reference_snapshot, other)
        self.assertEqual((result["value_changes"], result["unknown_count"]), (1, 0))

    def test_same_reference_handle_with_changed_identity_is_unknown(self):
        before = snapshot([prop("Target", object_reference(), "ObjectProperty")])
        for key, changed in (("index", 13), ("name", "Other"), ("path", "/Script/Other"), ("class", "Other")):
            after = copy.deepcopy(before)
            after["objects"][0]["properties"][0]["value"][key] = changed
            result = compare_snapshots(before, after)
            self.assertEqual((result["unknown_count"], result["value_changes"]), (1, 0))
            self.assertEqual(result["items"][0]["reason"], "object_reference_identity_mismatch")
            self.assertIsNone(result["equal_selected_values"])

    def test_reference_descriptors_remain_client_copies_and_detached(self):
        before = snapshot([prop("Target", None, "ObjectProperty")])
        after = snapshot([prop("Target", object_reference(), "ObjectProperty")])
        result = compare_snapshots(before, after)
        self.assertEqual(result["provenance"], "unverified")
        result["items"][0]["after"]["path"] = "/ChangedOutput"
        self.assertEqual(after["objects"][0]["properties"][0]["value"]["path"], "/Script/Probe.Default__Target")
        del after["source"]
        result = compare_snapshots(before, after)
        self.assertEqual(result["value_changes"], 0)
        self.assertGreater(result["unknown_count"], 0)

    def test_reference_descriptor_sessions_and_shape_are_validated(self):
        reference = object_reference()
        for key, changed in (("session_id", "session-2"), ("index", True), ("index", -1),
                             ("index", 4_000_000), ("handle", ""), ("handle", "h" * 81),
                             ("handle", "ж" * 41), ("name", "n" * 4097),
                             ("class", "c" * 4097), ("path", "p" * 8193), ("name", "ж" * 2049)):
            value = {**reference, key: changed}
            with self.subTest(key=key, value=repr(changed)[:30]):
                self.assert_invalid(snapshot([prop("Target", value, "ObjectProperty")]))
        self.assert_invalid(snapshot([prop("Target", {**reference, "extra": True}, "ObjectProperty")]))
        value = dict(reference)
        del value["index"]
        self.assert_invalid(snapshot([prop("Target", value, "ObjectProperty")]))
        self.assert_invalid(snapshot([prop("Target", reference, "StructProperty")]))
        self.assert_invalid(snapshot([prop("Target", reference, "ClassProperty")]))
        self.assert_invalid(snapshot([prop("Target", None, "ClassProperty")]))

    def test_unicode_object_names_use_fname_compatible_byte_limits(self):
        value = {**object_reference(), "name": "ж" * 600, "class": "名" * 1000}
        candidate = snapshot([prop("Target", value, "ObjectProperty")])
        self.assertTrue(compare_snapshots(candidate, candidate)["equal_selected_values"])
        value = {**object_reference(), "handle": "h" * 80, "name": "n" * 4096,
                 "class": "c" * 4096, "path": "p" * 8192}
        candidate = snapshot([prop("Target", value, "ObjectProperty")])
        self.assertTrue(compare_snapshots(candidate, candidate)["equal_selected_values"])

    def test_same_reference_type_cannot_be_hidden_as_loose_scalar(self):
        for value in (True, 12, "ref-1", [object_reference()]):
            self.assert_invalid(snapshot([prop("Target", value, "ObjectProperty")]))
        before = snapshot([prop("Target", object_reference(), "ObjectProperty")])
        after = snapshot([prop("Target", "ref-1", "StrProperty")])
        result = compare_snapshots(before, after)
        self.assertEqual((result["type_changes"], result["value_changes"]), (1, 1))
        self.assertEqual(result["items"][0]["before_value_kind"], "object_reference")


if __name__ == "__main__":
    unittest.main()
