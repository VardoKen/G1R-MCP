"""Подготовить WorldSubsystem и явную пробу reload/watch внутри artifacts, не запускать игру."""
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

PACKAGE_NAME = "G1RMCPWorldLifecycleProbe"
SCRIPT_RELATIVE = "Script/G1RMCPWorldLifecycleProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "world_lifecycle_probe"


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    spec = importlib.util.spec_from_file_location("_g1r_world_lifecycle_preparation", REPOSITORY_ROOT / "tools" / "prepare_class_probe.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.PACKAGE_NAME = PACKAGE_NAME
    implementation.SCRIPT_RELATIVE = SCRIPT_RELATIVE
    implementation.FIXTURE_ROOT = FIXTURE_ROOT
    result = implementation.prepare_probe(workspace_root, repository_root=repository_root)
    result["expected"] = {"verification": "unverified_until_automatic_callback_observed",
                          "class": "G1RMCPWorldPostInitializeProbe", "marker": 111,
                          "count": "PostInitializeCount", "read_method": "ReadPostInitializeCount",
                          "cdo_count": 0, "callback": "PostInitialize"}
    result["reload_and_watch"] = {
        "enabled_by_default": False,
        "probe_option": "--reload-and-watch",
        "methods": {"GetCurrentMapName": "FString", "GetCurrentLoadingScreenType": "ELoadingScreenType",
                    "ReloadCurrentMap": "void"},
        "watched_property": "PostInitializeCount",
        "loading_screen_type": "ELoadingScreenType::BlackScreen",
        "destination": "current_map",
    }
    result["limitations"] = ["PostInitialize не вызывается проверкой вручную",
                             "Подготовка не доказывает создание живой подсистемы или выполнение callback",
                             "Главное меню может не создать подсистему; отсутствие и нулевой счётчик не выдаются за успех",
                             "Подготовка не загружает мир, новую игру или сохранения",
                             "Перезагрузка текущей карты и watch выполняются только с явным --reload-and-watch"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовить собственный PostInitialize-счётчик")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT / "artifacts" / "world-lifecycle-dev11")
    args = parser.parse_args()
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
