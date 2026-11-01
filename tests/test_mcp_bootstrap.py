"""Первое stdio-подключение: обнаруживаемый жизненный цикл и чтение рабочей базы."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._asyncioRunner.get_loop().slow_callback_duration = 2.0

    def create_fixture(self, root):
        modkit = root / "Modkit"
        script = modkit / "Script/Official.as"
        script.parent.mkdir(parents=True)
        script.write_bytes(b"class UOfficial {}\r\n")
        workspace = root / "workspace"
        workspace.mkdir()
        game = root / "configured-game"
        parameters = StdioServerParameters(command=sys.executable,
            args=["-B", "-m", "g1r_mcp.server", "--modkit-root", str(modkit),
                  "--workspace-root", str(workspace), "--game-root", str(game)])
        return modkit, workspace, game, parameters

    async def call_ok(self, client, name, arguments=None):
        response = await client.call_tool(name, arguments or {})
        self.assertFalse(response.is_error, response.content)
        return response.structured_content

    async def call_error(self, client, name, arguments, code):
        response = await client.call_tool(name, arguments)
        self.assertTrue(response.is_error, response.content)
        message = next(block.text for block in response.content if block.type == "text")
        error = json.loads(message[message.index("{"):])
        self.assertEqual(error["code"], code)

    async def test_first_connection_exposes_real_lifecycle_and_effect_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modkit, workspace, game, parameters = self.create_fixture(root)
            (modkit / "Script/Official.as").write_bytes(
                b"class UOfficial\r\n{\r\n    int ReadValue(int Amount) const\r\n    { return Amount; }\r\n}\r\n")
            before = (modkit / "Script/Official.as").read_bytes()
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    initialized = await client.initialize()
                    instructions = initialized.instructions
                    self.assertIsInstance(instructions, str)
                    for important in ("GothicMod.json", "SHA-256", "g1r_native_enable", "g1r_native_status",
                                      "g1r_development_start", "g1r_native_load", "g1r_read_source_exact",
                                      "g1r_read_package_file_exact", "reconnect", "не повтор"):
                        self.assertIn(important, instructions)
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    self.assertEqual(len(tools), 41)
                    self.assertTrue(tools["g1r_list_source_functions"].annotations.read_only_hint)
                    self.assertTrue(tools["g1r_source_identity"].annotations.read_only_hint)
                    self.assertTrue(tools["g1r_verify_source_game"].annotations.read_only_hint)
                    status = await self.call_ok(client, "g1r_status")
                    self.assertEqual(status["sources"]["root"], str(modkit))
                    self.assertEqual(status["sources"]["identity_tool"], "g1r_source_identity")
                    self.assertEqual(status["workspace"]["root"], str(workspace))
                    self.assertEqual(status["game"]["configured_root"], str(game))
                    self.assertFalse(status["game"]["connection_checked"])
                    self.assertFalse(status["game"]["ready_known"])
                    self.assertFalse(game.exists())
                    catalog = await self.call_ok(client, "g1r_capabilities")
                    self.assertEqual(catalog["source_identity"], {
                        "tool": "g1r_source_identity", "snapshot_kind": "sequential_git_reads",
                        "source_only": True, "revision_source": "caller_label",
                        "worktree_scope": "repository", "game_compatibility_checked": False})
                    self.assertEqual(catalog["source_game_verification"], {
                        "tool": "g1r_verify_source_game", "snapshot_kind": "sequential_file_reads",
                        "correlation_scope": "modkit_script_subset_byte_equality", "paths": "server_configuration",
                        "runtime_compatibility_checked": False, "hash_executable_default": False})
                    identity = await self.call_ok(client, "g1r_source_identity")
                    self.assertIsInstance(identity["inspected_at"], str)
                    self.assertEqual(identity["snapshot_kind"], "sequential_git_reads")
                    self.assertIsNone(identity["configured_revision"])
                    self.assertEqual(identity["revision_source"], "caller_label")
                    self.assertTrue(identity["source_only"])
                    self.assertFalse(identity["game_compatibility_checked"])
                    self.assertIsNone(identity["git"]["head"])
                    self.assertIsNone(identity["git"]["working_tree_changed"])
                    self.assertIsNone(identity["git"]["configured_revision_matches_head"])
                    self.assertIn(identity["git"]["read_status"]["repository"], {"failed", "unavailable"})
                    self.assertIn(identity["git"]["read_status"]["head"], {"not_inspected", "unavailable"})
                    self.assertIn(identity["git"]["read_status"]["working_tree"], {"not_inspected", "unavailable"})
                    native_catalog = {item["name"]: item for item in catalog["native_tools"]}
                    for name in ("g1r_read_array", "g1r_read_struct"):
                        self.assertTrue(native_catalog[name]["read_only_hint"])
                        self.assertTrue(tools[name].annotations.read_only_hint)
                    self.assertEqual(catalog["source_functions"], {
                        "tool": "g1r_list_source_functions", "parser_kind": "lexical",
                        "result_field": "items", "prototype": "raw_source",
                        "item_fields": ["name", "owner", "namespace", "kind", "prototype", "path",
                                        "start_line", "end_line", "file_sha256", "root_kind"],
                        "runtime_signature_confirmed": False})
                    self.assertEqual(catalog["source_types"]["base_type"], "unresolved_source")
                    self.assertFalse(catalog["source_types"]["runtime_type_confirmed"])
                    source_types = await self.call_ok(client, "g1r_list_source_types", {"query": "UOfficial"})
                    self.assertEqual(source_types["parser_kind"], "lexical")
                    self.assertEqual(source_types["items"][0]["declaration"], "class UOfficial")
                    self.assertIsNone(source_types["items"][0]["base_type"])
                    self.assertEqual(source_types["items"][0]["file_sha256"], hashlib.sha256(before).hexdigest())
                    self.assertEqual(catalog["function_parameter_types"]["enum_entry_fields"], ["name", "value"])
                    string_input = catalog["invoke_arguments"]["string"]
                    self.assertIsNone(string_input["max_inputs"])
                    self.assertIsNone(string_input["max_allocated_input_bytes"])
                    self.assertEqual(string_input["max_utf16_units"], 2147483646)
                    array_input = catalog["invoke_arguments"]["object_array"]
                    self.assertIsNone(array_input["max_inputs"])
                    self.assertIsNone(array_input["max_allocated_input_bytes"])
                    self.assertEqual(array_input["max_elements"], 2147483647)
                    script_errors = catalog["script_runtime_errors"]
                    self.assertEqual(script_errors["error_code"], "angelscript_exception")
                    self.assertFalse(script_errors["stack_captured"])
                    self.assertFalse(script_errors["absence_of_logged_errors_proves_script_completion"])
                    functions = await self.call_ok(client, "g1r_list_source_functions", {
                        "query": "ReadValue", "relative_path": "Script/Official.as", "limit": 500, "offset": 0})
                    self.assertEqual(functions["parser_kind"], "lexical")
                    self.assertEqual(functions["total_matches"], 1)
                    self.assertEqual(functions["items"], [{
                        "name": "ReadValue", "owner": "UOfficial", "namespace": "", "kind": "function",
                        "prototype": "int ReadValue(int Amount) const", "path": "Script/Official.as",
                        "start_line": 3, "end_line": 3, "file_sha256": hashlib.sha256(before).hexdigest(),
                        "root_kind": "game_script"}])
                    development = catalog["development_tools"]["operations"]
                    self.assertEqual(set(development), {"g1r_development_recipe", "g1r_development_processes",
                                                         "g1r_development_start", "g1r_development_stop", "g1r_native_load",
                                                         "g1r_session_status", "g1r_session_acquire", "g1r_session_release", "g1r_compile_diagnostics"})
                    catalogued = {item["name"] for group in ("local_tools", "native_tools", "workspace_tools")
                                  for item in catalog[group]}
                    catalogued.update(development)
                    catalogued.add(catalog["snapshot_comparison"]["tool"])
                    self.assertEqual(catalogued, set(tools))
                    bootstrap = catalog["bootstrap"]
                    sequence = bootstrap["game_sequence"]
                    self.assertTrue(all(isinstance(step, dict) and step.get("condition") for step in sequence))
                    sequence_names = [step["tool"] for step in sequence]
                    self.assertTrue(set(sequence_names) <= set(tools))
                    self.assertLess(sequence_names.index("g1r_development_recipe"), sequence_names.index("g1r_development_start"))
                    self.assertLess(sequence_names.index("g1r_native_load"), sequence_names.index("g1r_native_enable"))
                    self.assertLess(sequence_names.index("g1r_object_functions"), sequence_names.index("g1r_invoke"))
                    self.assertEqual(sequence_names[-1], "g1r_development_stop")
                    self.assertTrue(set(bootstrap["existing_session_sequence"]) <= set(tools))
                    self.assertFalse(set(bootstrap["existing_session_sequence"]) & {"g1r_development_start", "g1r_native_load"})
                    self.assertTrue(set(bootstrap["edit_tools"]) <= set(tools))
                    examples = bootstrap["argument_examples"]
                    self.assertTrue(set(examples) <= set(tools))
                    self.assertEqual(set(examples["g1r_create_package"]["metadata"]), {"name", "title", "author", "modVersion"})
                    target = examples["g1r_snapshot"]["targets"][0]
                    self.assertEqual(set(target), {"handle", "properties"})
                    self.assertTrue(target["properties"])
                    self.assertEqual(examples["g1r_list_package_files"]["offset"], 0)
                    self.assertEqual(examples["g1r_invoke"]["arguments"], {"use_reflected_parameter_name": None})
                    self.assertTrue(bootstrap["script_patch_engine_apply_confirmed"])
                    self.assertEqual(catalog["invoke_arguments"]["rotator"], {
                        "type": "FRotator", "struct_path": "/Script/CoreUObject.Rotator",
                        "modes": ["input", "const_reference_input"], "size": 24,
                        "min_alignment": 8, "array_dim": 1, "units": "degrees",
                        "input_shape": {"rotator": {"Pitch": "finite_number", "Yaw": "finite_number", "Roll": "finite_number"}},
                        "reference_input_supported": True, "const_reference_required": True,
                        "layout_source": "reflected_function_parameter", "requires_signature_validation": True})
                    self.assertEqual(catalog["invoke_arguments"]["name"], {
                        "type": "FName", "input_shape": {"name": "string"},
                        "source_property_input_shape": {"name_from": {
                            "handle": "session_handle", "property_name": "reflected_property_name"}}})
                    self.assertEqual(catalog["invoke_returns"]["rotator"], {
                        "type": "FRotator", "return_type": "StructProperty",
                        "struct_path": "/Script/CoreUObject.Rotator", "passing": "value",
                        "size": 24, "min_alignment": 8, "array_dim": 1, "units": "degrees",
                        "result_shape": "object", "output_shape": {"Pitch": "finite_number", "Yaw": "finite_number", "Roll": "finite_number"},
                        "metadata_field": "result_metadata", "layout_source": "reflected_function_parameter",
                        "requires_signature_validation": True, "reference_return_supported": False})
                    self.assertEqual(catalog["invoke_returns"]["transform"], {
                        "type": "FTransform", "return_type": "StructProperty",
                        "struct_path": "/Script/CoreUObject.Transform", "passing": "value",
                        "size": 96, "array_dim": 1, "result_shape": "object",
                        "output_shape": {
                            "Rotation": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number", "W": "finite_number"},
                            "Translation": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number"},
                            "Scale3D": {"X": "finite_number", "Y": "finite_number", "Z": "finite_number"}},
                        "metadata_field": "result_metadata", "layout_source": "reflected_function_parameter",
                        "requires_signature_validation": True, "reference_return_supported": False})
                    self.assertEqual(catalog["invoke_returns"]["scalar_struct"], {
                        "return_type": "StructProperty", "result_shape": "object",
                        "field_types": ["BoolProperty", "IntProperty", "Int64Property", "UInt32Property",
                                        "UInt64Property", "FloatProperty", "DoubleProperty"],
                        "field_bytes": {"IntProperty": 4, "Int64Property": 8, "UInt32Property": 4,
                                        "UInt64Property": 8, "FloatProperty": 4, "DoubleProperty": 8},
                        "bool_layout": "reflected_bool_layout", "array_dim": 1,
                        "metadata_field": "result_metadata", "layout_source": "reflected_function_parameter",
                        "passing": "value", "own_fields_only": True,
                        "lifecycle": "plain_default_copy", "custom_lifecycle_supported": False,
                        "source_contract_required": True,
                        "input_supported": False, "reference_return_supported": False,
                        "nested_fields_supported": False, "string_fields_supported": False})
                    self.assertIn("session", bootstrap["ownership"])
                    self.assertIn("find_objects_again", bootstrap["reconnect"])
                    self.assertIn("never_automatically_retry", bootstrap["uncertain_effect"])
                    for name in ("g1r_status", "g1r_capabilities", "g1r_development_recipe", "g1r_development_processes",
                                 "g1r_native_status", "g1r_find_objects", "g1r_object_functions", "g1r_read_source_exact",
                                 "g1r_read_package_file_exact"):
                        self.assertTrue(tools[name].annotations.read_only_hint)
                    for name in ("g1r_development_start", "g1r_native_load", "g1r_native_enable",
                                 "g1r_invoke", "g1r_development_stop", "g1r_write_package_file"):
                        self.assertFalse(tools[name].annotations.read_only_hint)
            self.assertEqual((modkit / "Script/Official.as").read_bytes(), before)
            self.assertEqual(list(workspace.iterdir()), [])
            self.assertFalse(game.exists())

    async def test_paginated_package_listing_covers_large_working_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modkit, workspace, game, parameters = self.create_fixture(root)
            package = workspace / "LargePackage"
            scripts = package / "Script"
            scripts.mkdir(parents=True)
            descriptor = package / "GothicMod.json"
            descriptor.write_text(json.dumps({"name": "LargePackage", "title": "Fixture", "author": "Test",
                                              "modVersion": "1.0.0"}), encoding="utf-8")
            for index in range(205):
                (scripts / f"File{index:03}.as").write_text(f"// source {index}\n", encoding="utf-8")
            before = {str(path.relative_to(package)): path.read_bytes() for path in package.rglob("*") if path.is_file()}
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "g1r_list_package_files")
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertIn("offset", tool.input_schema["properties"])
                    pages = []
                    offset = 0
                    for _ in range(4):
                        page = await self.call_ok(client, "g1r_list_package_files", {
                            "relative_path": "LargePackage", "limit": 100, "offset": offset,
                        })
                        self.assertEqual(page["offset"], offset)
                        self.assertFalse(page["snapshot_atomic"])
                        self.assertEqual(page["total"], 206)
                        self.assertLessEqual(len(page["items"]), 100)
                        pages.extend(item["path"] for item in page["items"])
                        if page["next_offset"] is None:
                            self.assertFalse(page["has_more"])
                            break
                        self.assertTrue(page["has_more"])
                        self.assertGreater(page["next_offset"], offset)
                        offset = page["next_offset"]
                    else:
                        self.fail("Пагинация не завершилась в пределах полного индекса fixture")
                    expected = ["GothicMod.json"] + [f"Script/File{index:03}.as" for index in range(205)]
                    self.assertEqual(pages, expected)
                    self.assertEqual(len(set(pages)), 206)
                    for invalid in (-1, 10001):
                        await self.call_error(client, "g1r_list_package_files", {
                            "relative_path": "LargePackage", "limit": 100, "offset": invalid,
                        }, "invalid_arguments")
            self.assertEqual({str(path.relative_to(package)): path.read_bytes() for path in package.rglob("*") if path.is_file()}, before)
            self.assertFalse(game.exists())


    async def test_source_game_verification_reports_subset_without_runtime_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modkit, workspace, _, parameters = self.create_fixture(root)
            game = root / "steamapps/common/Game"
            scripts = game / "G1R/Script"
            scripts.mkdir(parents=True)
            source = (modkit / "Script/Official.as").read_bytes()
            (scripts / "Official.as").write_bytes(source)
            (scripts / "GameOnly.as").write_bytes(b"class UGameOnly {}\n")
            (root / "steamapps/appmanifest_1297900.acf").write_text(
                '"AppState" { "appid" "1297900" "buildid" "26295476" "installdir" "Game" }', encoding="utf-8")
            executable = game / "G1R/Binaries/Win64/G1R-Win64-Shipping.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"synthetic executable")
            arguments = list(parameters.args)
            arguments[arguments.index("--game-root") + 1] = str(game)
            parameters = StdioServerParameters(command=sys.executable, args=arguments)
            async with stdio_client(parameters) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as client:
                    await client.initialize()
                    report = await self.call_ok(client, "g1r_verify_source_game")
                    self.assertTrue(report["ok"])
                    self.assertEqual(report["manifest"]["appid"], "1297900")
                    self.assertEqual(report["manifest"]["buildid"], "26295476")
                    self.assertEqual(report["snapshot_kind"], "sequential_file_reads")
                    self.assertEqual(report["source_identity_observation"], "before_file_comparison")
                    self.assertEqual(report["correlation_scope"], "modkit_script_subset_byte_equality")
                    self.assertTrue(report["subset_matches_game_scripts"])
                    self.assertFalse(report["game_only_scripts_checked"])
                    self.assertFalse(report["runtime_compatibility_checked"])
                    self.assertEqual(report["scripts"]["source_count"], 1)
                    self.assertEqual(report["scripts"]["identical"], 1)
                    self.assertIsNone(report["scripts"]["max_list_entries"])
                    self.assertFalse(report["scripts"]["lists_truncated"])
                    self.assertIsInstance(report["inspection_started_at"], str)
                    self.assertIsInstance(report["inspection_finished_at"], str)
                    self.assertFalse(report["source_identity"]["game_compatibility_checked"])
                    self.assertNotIn("executable", report)
                    hashed = await self.call_ok(client, "g1r_verify_source_game", {"hash_executable": True})
                    self.assertEqual(hashed["executable"]["sha256"], hashlib.sha256(executable.read_bytes()).hexdigest())
                    (scripts / "Official.as").write_bytes(b"class UDifferent {}\n")
                    mismatch = await self.call_ok(client, "g1r_verify_source_game")
                    self.assertFalse(mismatch["subset_matches_game_scripts"])
                    self.assertEqual(mismatch["scripts"]["different_files"], ["Official.as"])
                    self.assertFalse(mismatch["runtime_compatibility_checked"])
            self.assertEqual((modkit / "Script/Official.as").read_bytes(), source)
            self.assertEqual(list(workspace.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
