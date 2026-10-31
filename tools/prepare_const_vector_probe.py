"""Подготовить собственную const FVector ref-fixture без запуска игры."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
from g1r_mcp.packages import PackageError
from g1r_mcp.workspace import WorkspaceError

PACKAGE_NAME = "G1RMCPConstVectorProbe"
SCRIPT_RELATIVE = "Script/G1RMCPConstVectorProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/const_vector_probe"


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    spec = importlib.util.spec_from_file_location("_g1r_const_vector_preparation", REPOSITORY_ROOT / "tools/prepare_class_probe.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.PACKAGE_NAME = PACKAGE_NAME
    implementation.SCRIPT_RELATIVE = SCRIPT_RELATIVE
    implementation.FIXTURE_ROOT = FIXTURE_ROOT
    result = implementation.prepare_probe(workspace_root, repository_root=repository_root)
    vector = {"X": 1.25, "Y": -2.5, "Z": 3.75}
    result["expected"] = {
        "verification": "unverified_until_runtime_reflection_and_calls",
        "host_cdo": "Default__G1RMCPConstVectorProbe", "marker": 181,
        "properties": [{"name": "CallsCount", "source_type": "int", "initial_value": 0},
                       {"name": "StoredVector", "source_type": "FVector", "initial_value": vector}],
        "methods": [
            {"name": "ReadMarker", "inputs": [], "result": "int"},
            {"name": "EchoConstVector", "inputs": [{"name": "Input", "type": "const FVector&in"}],
             "result": "FVector", "increments": "CallsCount"},
            {"name": "GetVector", "inputs": [], "result": "FVector", "expected_result": vector},
            {"name": "ReadCallsCount", "inputs": [], "result": "int"},
            {"name": "ResetProbe", "inputs": [], "result": "void"}],
        "syntax_sources": {
            "const_vector_reference": "Script/AI/NativeAICommands.as:246,252",
            "ufunction_const_struct_in": "Script/AI/CharacterAI_Gothic.as:1292",
            "ufunction_const_struct_in_second": "Script/GAS/Abilities/Locomotion/Actors/RagdollFallingActor.as:40"}}
    result["limitations"] = [
        "Const FVector&in в UFUNCTION — согласованная композиционная runtime-гипотеза по текущим AS-примерам",
        "Подготовка не подтверждает компиляцию, CPF Const/Reference/Out-флаги или числовой ABI FVector",
        "EchoConstVector не пишет во вход; только CallsCount своего CDO меняется и сбрасывается ResetProbe",
        "Mutable inout, pure out и другие ref-типы не включены",
        "StoredVector остаётся неизменным; игровые объекты, мир и сохранения не используются"]
    return result


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage:", "Использование:", 1).replace("options:", "Параметры:", 1)
    parser = RussianArgumentParser(description="Подготовить собственную const FVector fixture внутри artifacts", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT / "artifacts/const-vector-probe-dev18",
                        help="Отдельный каталог внутри artifacts")
    args = parser.parse_args(argv)
    try:
        result = prepare_probe(args.workspace_root)
    except (PackageError, WorkspaceError, OSError) as error:
        print(json.dumps({"code": getattr(error, "code", "access_error"), "message": str(error),
                          "notes": getattr(error, "__notes__", [])}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
