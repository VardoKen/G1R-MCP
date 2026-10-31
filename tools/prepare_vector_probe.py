"""Подготовить собственную FVector fixture внутри artifacts без запуска игры."""
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

PACKAGE_NAME = "G1RMCPVectorProbe"
SCRIPT_RELATIVE = "Script/G1RMCPVectorProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/vector_probe"


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    spec = importlib.util.spec_from_file_location("_g1r_vector_preparation", REPOSITORY_ROOT / "tools/prepare_class_probe.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.PACKAGE_NAME = PACKAGE_NAME
    implementation.SCRIPT_RELATIVE = SCRIPT_RELATIVE
    implementation.FIXTURE_ROOT = FIXTURE_ROOT
    result = implementation.prepare_probe(workspace_root, repository_root=repository_root)
    vector = {"X": 1.25, "Y": -2.5, "Z": 3.75}
    result["expected"] = {
        "verification": "unverified_until_runtime_reflection_and_calls",
        "host_cdo": "Default__G1RMCPVectorProbe", "marker": 161,
        "properties": [{"name": "CallsCount", "source_type": "int", "initial_value": 0},
                       {"name": "StoredVector", "source_type": "FVector", "initial_value": vector}],
        "methods": [
            {"name": "ReadMarker", "inputs": [], "result": "int"},
            {"name": "EchoVector", "inputs": [{"name": "Input", "type": "FVector"}],
             "result": "FVector", "increments": "CallsCount"},
            {"name": "GetVector", "inputs": [], "result": "FVector", "expected_result": vector},
            {"name": "WriteVector", "inputs": [], "outputs": [{"name": "Output", "type": "FVector"}],
             "result": "bool", "expected_result": True, "expected_out_parameters": {"Output": vector},
             "increments": "CallsCount"},
            {"name": "ReadCallsCount", "inputs": [], "result": "int"},
            {"name": "ResetProbe", "inputs": [], "result": "void"}],
        "syntax_sources": {
            "value_input_return": "Script/GAS/GASCharacterMixins.as:1364",
            "out_parameter": "Script/AI/States/FightAI/CombatState/CombatMoves.as:1877"}}
    result["limitations"] = [
        "Подготовка не подтверждает компиляцию, регистрацию или runtime ABI FVector",
        "Размер структуры, компоненты и CPF-флаги направлений сначала подтверждаются reflection",
        "FVector inout и const-ref не включены: точный текущий AngelScript-синтаксис не подтверждён",
        "Вызовы меняют только CallsCount своего CDO; ResetProbe восстанавливает ноль",
        "StoredVector остаётся неизменным; игровые объекты, мир и сохранения не используются"]
    return result


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage:", "Использование:", 1).replace("options:", "Параметры:", 1)
    parser = RussianArgumentParser(description="Подготовить собственную FVector fixture внутри artifacts", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT / "artifacts/vector-probe-dev16",
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
