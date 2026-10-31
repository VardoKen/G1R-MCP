"""Наблюдать собственный PostInitialize через MCP без ручного вызова callback."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))
from probe_object_mcp import _caller, _single_error
from probe_cleanup import deferred_sigint

HOST = "G1RMCPWorldPostInitializeProbe"
COUNT = "PostInitializeCount"
READ_METHOD = "ReadPostInitializeCount"
MARKER = 111


async def exercise(call, *, wait_seconds=3, clock=time.monotonic, sleep=asyncio.sleep):
    # Отдельный экземпляр существующей read-only проверки сохраняет её границы,
    # не меняя глобальное состояние IngameTool-пробы и её fixture.
    spec = importlib.util.spec_from_file_location("_g1r_world_lifecycle_observation", REPOSITORY_ROOT / "tools" / "probe_lifecycle_mcp.py")
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    implementation.TOOL = HOST
    implementation.CLASSES = {HOST: {"marker": MARKER, "fields": {COUNT: READ_METHOD}}}
    implementation.READ_METHODS = {"ReadMarker", READ_METHOD}
    report = await implementation.exercise(call, wait_seconds=wait_seconds, clock=clock, sleep=sleep,
                                           positive_counter=COUNT)
    world = report.pop("tool")
    if world["status"] == "automatic_initialize_confirmed":
        world["status"] = "automatic_post_initialize_confirmed"
    report["world"] = world
    if report["status"] != "completed":
        report["status"] = "not_tested_not_triggered"
    report["callback"] = "PostInitialize"
    report["gameplay_ready_confirmed"] = False
    report["limitations"] = ["Положительный счётчик доказывает только выполнение PostInitialize собственной подсистемы",
                             "Отсутствие экземпляра или нулевой счётчик в главном меню не считается подтверждённым lifecycle",
                             "Начало gameplay, полный жизненный цикл и сохранения не проверяются"]
    return report


async def run_probe(args):
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "g1r_mcp.server", "--modkit-root", str(args.modkit_root),
        "--workspace-root", str(args.workspace_root), "--native-port", str(args.native_port)], cwd=str(REPOSITORY_ROOT))
    with deferred_sigint():
        async with stdio_client(parameters) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as client:
                initialized = await client.initialize()
                report = await exercise(_caller(client), wait_seconds=args.wait_seconds)
                report["server_version"] = initialized.server_info.version
                return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Наблюдать собственный PostInitialize без загрузки игрового мира")
    parser.add_argument("--modkit-root", type=Path, default=REPOSITORY_ROOT.parent / "G1R-Modkit")
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--native-port", type=int, default=27123)
    parser.add_argument("--wait-seconds", type=float, default=3)
    args = parser.parse_args()
    try:
        report = asyncio.run(run_probe(args))
    except Exception as error:
        cause = _single_error(error)
        print(json.dumps({"code": getattr(cause, "code", "probe_failed"), "message": str(cause)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
