"""Подготовить собственную AS-обёртку FHitResult без запуска игры."""
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

PACKAGE_NAME = "G1RMCPHitResultWrapperProbe"
SCRIPT_RELATIVE = "Script/G1RMCPHitResultWrapperProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/hit_result_wrapper_probe"


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    spec = importlib.util.spec_from_file_location("_g1r_hit_result_wrapper_preparation", REPOSITORY_ROOT / "tools/prepare_class_probe.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.PACKAGE_NAME = PACKAGE_NAME
    implementation.SCRIPT_RELATIVE = SCRIPT_RELATIVE
    implementation.FIXTURE_ROOT = FIXTURE_ROOT
    result = implementation.prepare_probe(workspace_root, repository_root=repository_root)
    result["expected"] = {
        "verification": "unverified_until_runtime_reflection_and_calls",
        "host_class": "G1RMCPHitResultWrapperProbe", "marker": 251,
        "host_selection": "live_non_cdo_instance",
        "source_base_class": "UScriptWorldSubsystem", "automatic_instance_verified": False,
        "properties": [{"name": "CallsCount", "source_type": "int", "initial_value": 0}],
        "methods": [
            {"name": "ReadMarker", "inputs": [], "result": "int"},
            {"name": "HasWorld", "inputs": [], "result": "bool", "increments": None,
             "checks": "GetWorld() != nullptr"},
            {"name": "TraceImpactPoint", "inputs": [{"name": "Start", "type": "FVector"},
                                                     {"name": "End", "type": "FVector"},
                                                     {"name": "ActorsToIgnore", "type": "const TArray<AActor>&in"}],
             "outputs": [{"name": "ImpactPoint", "type": "FVector&out"}],
             "result": "bool", "increments": "CallsCount", "world_context_verified": False,
             "requires": "HasWorld", "no_world_behavior": "false_zero_without_trace"},
            {"name": "ReadDefaultImpactPoint", "inputs": [], "result": "FVector",
             "result_validation": "finite_xyz", "record_observed_result": True, "increments": "CallsCount",
             "calls_trace": False},
            {"name": "ReadCallsCount", "inputs": [], "result": "int"},
            {"name": "ResetProbe", "inputs": [], "result": "void", "resets": ["CallsCount"]}],
        "trace_settings": {"collision_channel": "ECollisionChannel::ECC_Visibility",
                           "trace_type_conversion": "UCollisionProfile::ConvertToTraceType",
                           "trace_complex": False, "draw_debug": "EDrawDebugTrace::None", "ignore_self": True,
                           "miss_impact_point": {"X": 0.0, "Y": 0.0, "Z": 0.0}},
        "syntax_sources": {
            "uobject_get_world_api": "https://alkimiainteractive.github.io/G1R-Modkit/script-reference/class_u_object.html",
            "get_world_usage": "Script/CheatMode/Commands/ChangeTimeCommand.as:32",
            "world_subsystem_class": "Script/AI/States/FightAI/ManagedFightSubsystem.as:13",
            "own_world_subsystem_fixture": "tests/fixtures/world_lifecycle_probe/Script/G1RMCPWorldLifecycleProbe.as:3",
            "line_trace_single_arguments": "Script/AI/States/AIState_FollowCharacterAggressive.as:56-59",
            "visibility_trace_conversion": "Script/AI/States/AIState_FollowCharacterAggressive.as:47",
            "actor_array_declaration": "Script/AI/States/AIState_FollowCharacterAggressive.as:50",
            "ufunction_const_array_in": "Script/AI/States/FightAI/AIGroup_ConflictInstance.as:880-881",
            "ufunction_vector_out": "Script/AI/States/FightAI/CombatState/CombatMoves.as:1876-1877",
            "hit_result_default_constructor": "Script/Regions/Traits/GameplayEffect/Damage/Base/DamageTraits.as:29",
            "hit_result_impact_point": "Script/Gameplay/Magic/Components/Spell/Summon/SummonManagerComponent.as:53",
            "vector_zero": "Script/AI/States/FightAI/WatchFightState/AIState_WatchFight.as:245"}}
    result["limitations"] = [
        "TraceImpactPoint UFUNCTION — согласованная композиционная runtime-гипотеза по текущим AS-примерам",
        "Подготовка не подтверждает компиляцию, CPF-флаги, создание живой подсистемы, её world context или выполнение трассировки",
        "HasWorld читает GetWorld без изменения счётчика; при false проба должна пропустить TraceImpactPoint",
        "TraceImpactPoint при отсутствии мира возвращает false и ZeroVector до вызова System::LineTraceSingle",
        "FHitResult создаётся и уничтожается внутри AngelScript; его layout не передаётся через MCP",
        "ReadDefaultImpactPoint создаёт локальный FHitResult без трассировки; проверяются конечные XYZ и сохраняется фактический результат",
        "TraceImpactPoint читает коллизию мира и возвращает FVector; actors не создаются и не меняются",
        "ActorsToIgnore содержит только runtime-дескрипторы AActor; runtime-совместимость типа требует проверки",
        "Нужно выбрать живой экземпляр собственной UScriptWorldSubsystem и проверить HasWorld; CDO не использовать как доказательство контекста",
        "Меняется только CallsCount собственной подсистемы; ResetProbe сбрасывает счётчик",
        "Установка, запуск игры, сохранения и пользовательские моды не входят в подготовку"]
    return result


def main(argv: list[str] | None = None) -> int:
    class RussianArgumentParser(argparse.ArgumentParser):
        def format_help(self):
            return super().format_help().replace("usage:", "Использование:", 1).replace("options:", "Параметры:", 1)
    parser = RussianArgumentParser(description="Подготовить собственную AS-обёртку FHitResult внутри artifacts", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="Показать справку")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT / "artifacts/hit-result-wrapper-world-probe-dev25",
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
