"""Подготовить собственную FLinearColor value-fixture без запуска игры."""
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

PACKAGE_NAME = "G1RMCPLinearColorProbe"
SCRIPT_RELATIVE = "Script/G1RMCPLinearColorProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/linear_color_probe"


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    spec = importlib.util.spec_from_file_location("_g1r_linear_color_preparation", REPOSITORY_ROOT / "tools/prepare_class_probe.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.PACKAGE_NAME = PACKAGE_NAME
    implementation.SCRIPT_RELATIVE = SCRIPT_RELATIVE
    implementation.FIXTURE_ROOT = FIXTURE_ROOT
    result = implementation.prepare_probe(workspace_root, repository_root=repository_root)
    color = {"R": 0.25, "G": -0.5, "B": 2.0, "A": 0.125}
    inputs = [{"name": "Input", "type": "FLinearColor"}]
    result["expected"] = {
        "verification": "unverified_until_runtime_reflection_and_calls",
        "host_cdo": "Default__G1RMCPLinearColorProbe", "marker": 231,
        "properties": [{"name": "CallsCount", "source_type": "int", "initial_value": 0}],
        "methods": [
            {"name": "ReadMarker", "inputs": [], "result": "int"},
            {"name": "ReadColorR", "inputs": inputs, "result": "float", "increments": "CallsCount"},
            {"name": "EchoColor", "inputs": inputs, "result": "FLinearColor", "increments": "CallsCount"},
            {"name": "MakeStoredColor", "inputs": [], "result": "FLinearColor",
             "expected_result": color, "increments": "CallsCount"},
            {"name": "ReadCallsCount", "inputs": [], "result": "int"},
            {"name": "ResetProbe", "inputs": [], "result": "void", "resets": ["CallsCount"]}],
        "color_case": {"input": color, "read_r": 0.25, "echo_result": color},
        "syntax_sources": {
            "ufunction_color_value_input": "Script/FX/Blood/BloodCharacterData.as:4-5",
            "color_rgba_constructor": "Script/Camera/ConversationPresetsV2.as:147",
            "color_component_r": "Script/Camera/ConversationCameraConfig.as:403",
            "ufunction_value_struct_return": "Script/GAS/GASCharacterMixins.as:1363-1364"}}
    result["limitations"] = [
        "FLinearColor UFUNCTION-возврат — согласованная композиционная runtime-гипотеза по текущим AS-примерам",
        "Подготовка не подтверждает компиляцию, CPF-флаги, FLinearColor layout или числовой ABI",
        "AS float для ReadColorR записан как исходный тип; фактический DoubleProperty требует runtime-проверки",
        "Ожидаемые компоненты включают отрицательное и HDR-значение; фикстура не ограничивает цвет диапазоном 0..1",
        "Const/ref/out и массивы цвета не включены",
        "Меняется только CallsCount собственного CDO; ResetProbe сбрасывает счётчик",
        "Игровые объекты мира, spawn и сохранения не используются"]
    return result


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage:", "Использование:", 1).replace("options:", "Параметры:", 1)
    parser = RussianArgumentParser(description="Подготовить собственную FLinearColor value-fixture внутри artifacts", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT / "artifacts/linear-color-probe-dev23",
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
