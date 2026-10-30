"""Подготовить безопасную fixture проверки ScriptPatches; игру не запускать.

Официальный Modkit документирует патчи базовых Script/. Применение к новым
исходникам того же пакета не подтверждено и является предметом этой проверки.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
from g1r_mcp.packages import PackageError, validate_package
from g1r_mcp.workspace import (PackageWorkspace, WorkspaceError, _checked,
                               _destination_lock, _guard_directories, _read_bytes)

PACKAGE_NAME = "G1RMCPPatchProbe"
SCRIPT_RELATIVE = "Script/G1RMCPPatchProbe.as"
PATCH_RELATIVE = "ScriptPatches/Marker.patch"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/patch_probe"


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _cleanup_owned(stage: Path, workspace: Path, files: dict, directories: dict) -> None:
    """Удалить только наши неизменённые файлы; подозрительное дерево сохранить."""
    try:
        with _guard_directories(stage, workspace):
            if _identity(stage) != directories.get("."):
                return
            # Сначала проверяется вся цепь каждого родителя: cleanup не следует ссылке.
            for relative, identity in directories.items():
                directory = _checked(stage if relative == "." else stage / relative, stage, directory=True)
                if _identity(directory) != identity:
                    return
            for relative in files:
                _checked((stage / relative).parent, stage, directory=True)
            for relative, (identity, digest) in files.items():
                path = stage / relative
                with _guard_directories(path.parent, workspace):
                    checked = _checked(path, stage)
                    if _identity(checked) != identity:
                        continue
                    if hashlib.sha256(_read_bytes(checked, stage, 2 * 1024 * 1024)).hexdigest() != digest:
                        continue
                    checked.unlink()
            for relative, identity in reversed(tuple(directories.items())):
                if relative == ".":
                    continue
                directory = _checked(stage / relative, stage, directory=True)
                if _identity(directory) == identity and not any(directory.iterdir()):
                    directory.rmdir()
        if _identity(_checked(stage, workspace, directory=True)) == directories.get(".") and not any(stage.iterdir()):
            stage.rmdir()
    except (WorkspaceError, OSError):
        # Изменённое или неожиданное содержимое сохраняется; исходная ошибка не маскируется.
        return


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    repository = _checked(repository_root, directory=True)
    artifacts = _checked(repository / "artifacts", repository, directory=True)
    workspace = _checked(workspace_root, artifacts, directory=True)
    if workspace == artifacts:
        raise PackageError("unsafe_path", "Нужен отдельный существующий каталог внутри artifacts")
    fixture = _checked(FIXTURE_ROOT, directory=True)
    validation = validate_package(fixture, ".")
    source_files = ("GothicMod.json", SCRIPT_RELATIVE, PATCH_RELATIVE)
    if (not validation["valid_structure"] or validation["script_count"] != 1
            or validation["patch_count"] != 1 or validation["metadata"].get("name") != PACKAGE_NAME):
        raise PackageError("fixture_invalid", "Fixture патча не соответствует рецепту")
    contents = {name: _checked(fixture / name, fixture).read_bytes() for name in source_files}
    source = contents[SCRIPT_RELATIVE]
    diff = contents[PATCH_RELATIVE]
    if (source.count(b"return 10;") != 1 or b"+        return 11;" not in diff
            or b"a/" + SCRIPT_RELATIVE.encode() not in diff
            or not re.search(rb"UCLASS\(\)\s+class UG1RMCPPatchProbe\s*:\s*UObject\s*\{", source)
            or b"@@ -5,6 +5,6 @@ class UG1RMCPPatchProbe : UObject" not in diff):
        raise PackageError("fixture_invalid", "Маркер или путь патча не соответствует рецепту")
    target = workspace / PACKAGE_NAME
    stage: Path | None = None
    owned_files: dict = {}
    owned_directories: dict = {}
    with _destination_lock(target), _guard_directories(workspace, artifacts):
        if os.path.lexists(target):
            raise PackageError("already_exists", "Проверочный пакет уже существует")
        try:
            stage = Path(tempfile.mkdtemp(prefix=".g1r-mcp-patch-probe-", dir=workspace))
            owned_directories["."] = _identity(stage)
            with _guard_directories(stage, workspace):
                for relative in ("Script", "ScriptPatches"):
                    (stage / relative).mkdir()
                    owned_directories[relative] = _identity(stage / relative)
                writer = PackageWorkspace(workspace)
                for relative, raw in contents.items():
                    with _guard_directories((stage / relative).parent, workspace):
                        result = writer.write_package_file(stage.name, relative, raw.decode("utf-8"))
                        owned_files[relative] = (_identity(_checked(stage / relative, stage)), result["sha256"])
                copied = validate_package(stage, ".")
                if not copied["valid_structure"] or copied["script_count"] != 1 or copied["patch_count"] != 1:
                    raise PackageError("fixture_invalid", "Копия fixture не прошла структурную проверку")
            if os.name != "nt":
                raise PackageError("unsupported_environment", "Атомарная подготовка поддержана на Windows")
            stage.rename(target)
            stage = None
        finally:
            if stage is not None:
                _cleanup_owned(stage, workspace, owned_files, owned_directories)
    return {
        "package": PACKAGE_NAME, "package_path": str(target),
        "validation": copied, "verification": "not_performed",
        "files_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()},
        "launch_options": [f"-ModsDirectory={workspace.as_posix()}", f"-Mods={PACKAGE_NAME}"],
        "runtime_probe": {"class": "UG1RMCPPatchProbe", "object_name": "Default__G1RMCPPatchProbe",
                          "function": "ReadPatchMarker", "arguments": {},
                          "expected_signature": {"input_count": 0, "return": "IntProperty", "return_bytes": 4}},
        "interpretation": {"11": "Патч применился к новому исходнику этого пакета",
                           "10": "Патч не изменил новое тело функции; причина требует диагностики",
                           "missing_class_or_failure": "Порядок, путь или загрузка не подтверждены"},
        "limitations": ["Документы описывают патчи базовых Script/, обработка новых исходников пакета не подтверждена",
                        "Fixture не изменяет официальные исходники и не использует игровые механики",
                        "Подготовка и стандартный unified diff не доказывают применение или компиляцию игрой"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Подготовить изолированную проверку ScriptPatches без запуска игры")
    parser.add_argument("--workspace-root", type=Path, required=True, help="Существующий отдельный каталог внутри artifacts")
    arguments = parser.parse_args(argv)
    try:
        result = prepare_probe(arguments.workspace_root)
    except (PackageError, WorkspaceError, OSError) as error:
        message = str(error) if isinstance(error, (PackageError, WorkspaceError)) else "Не удалось подготовить проверочный пакет"
        print(json.dumps({"code": getattr(error, "code", "access_error"), "message": message}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
