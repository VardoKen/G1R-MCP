"""Подготовить объектную AngelScript-fixture внутри artifacts без запуска игры."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from g1r_mcp.packages import PackageError, validate_package
from g1r_mcp.workspace import PackageWorkspace, WorkspaceError, _checked, _destination_lock, _guard_directories, _read_bytes

PACKAGE_NAME = "G1RMCPObjectProbe"
SCRIPT_RELATIVE = "Script/G1RMCPObjectProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "object_probe"


def _fixture() -> tuple[dict, bytes]:
    validation = validate_package(FIXTURE_ROOT, ".")
    source = (FIXTURE_ROOT / SCRIPT_RELATIVE).read_bytes()
    if (not validation["valid_structure"] or validation["script_count"] != 1
            or validation["metadata"].get("name") != PACKAGE_NAME):
        raise PackageError("fixture_invalid", "Объектная fixture не прошла структурную проверку")
    return validation["metadata"], source


def _workspace_path(path: Path, repository_root: Path) -> Path:
    repository = _checked(Path(repository_root), directory=True)
    artifacts = _checked(repository / "artifacts", repository, directory=True)
    absolute = Path(os.path.abspath(path))
    if absolute == artifacts or not absolute.is_relative_to(artifacts):
        raise PackageError("unsafe_path", "Рабочая база проверки должна находиться внутри artifacts")
    # Новый каталог создаётся только в существующем проверенном родителе.
    parent = _checked(absolute.parent, artifacts, directory=True)
    if os.path.lexists(absolute):
        return _checked(absolute, artifacts, directory=True)
    with _destination_lock(absolute), _guard_directories(parent, repository):
        if not os.path.lexists(absolute):
            absolute.mkdir()
        return _checked(absolute, artifacts, directory=True)


def _expected() -> dict:
    return {
        "verification": "unverified_until_runtime_reflection_and_calls",
        "classes": [
            {"class": "UG1RMCPObjectProbe", "object_name": "Default__G1RMCPObjectProbe", "marker": 41},
            {"class": "UG1RMCPDerivedObjectProbe", "object_name": "Default__G1RMCPDerivedObjectProbe", "marker": 41},
            {"class": "UG1RMCPOtherObjectProbe", "object_name": "Default__G1RMCPOtherObjectProbe", "marker": 99},
        ],
        "properties": [
            {"name": "StoredObject", "source_type": "UObject", "initial_value": None},
            {"name": "StoredTypedObject", "source_type": "UG1RMCPObjectProbe", "initial_value": None},
        ],
        "methods": [
            {"name": "ReadMarker", "arguments": [], "result": "int", "expected_value": 41},
            {"name": "ReadSelf", "arguments": [], "result": "UObject", "identity": "called_object"},
            {"name": "EchoObject", "arguments": [{"name": "Input", "type": "UObject"}], "result": "UObject"},
            {"name": "EchoTypedObject", "arguments": [{"name": "Input", "type": "UG1RMCPObjectProbe"}], "result": "UG1RMCPObjectProbe"},
            {"name": "IsNullObject", "arguments": [{"name": "Input", "type": "UObject"}], "result": "bool"},
            {"name": "StoreObject", "arguments": [{"name": "Input", "type": "UObject"}], "result": "void", "effects": "own_probe_field"},
            {"name": "StoreTypedObject", "arguments": [{"name": "Input", "type": "UG1RMCPObjectProbe"}], "result": "void", "effects": "own_probe_field"},
        ],
    }


def _record_owned(created: dict[Path, tuple[int, int, str | None]], path: Path, boundary: Path, *, directory: bool = False) -> None:
    checked = _checked(path, boundary, directory=directory)
    info = checked.stat()
    content_hash = None if directory else hashlib.sha256(_read_bytes(checked, boundary, 2 * 1024 * 1024)).hexdigest()
    created[path] = (info.st_dev, info.st_ino, content_hash)


def _assert_owned(created: dict[Path, tuple[int, int, str | None]], path: Path, boundary: Path, *, directory: bool = False) -> None:
    checked = _checked(path, boundary, directory=directory)
    info = checked.stat()
    content_hash = None if directory else hashlib.sha256(_read_bytes(checked, boundary, 2 * 1024 * 1024)).hexdigest()
    if path not in created or created[path] != (info.st_dev, info.st_ino, content_hash):
        raise WorkspaceError("unsafe_cleanup", "Созданный путь был заменён; остатки сохранены без удаления")


def _cleanup_stage(stage: Path, workspace: Path, created: dict[Path, tuple[int, int, str | None]]) -> None:
    """Удалять только собственные пути после проверки всей цепи до первого unlink."""
    _assert_owned(created, stage, workspace, directory=True)
    package = stage / PACKAGE_NAME
    script = package / "Script"
    descriptor = package / "GothicMod.json"
    source = package / SCRIPT_RELATIVE
    with _guard_directories(stage, workspace):
        entries = set(stage.iterdir())
        if entries not in (set(), {package}):
            raise WorkspaceError("unsafe_cleanup", "Временный каталог содержит чужие пути; остатки сохранены")
        if entries:
            # All parents and identities are checked before any file is deleted.
            for directory in (package, script):
                _assert_owned(created, directory, stage, directory=True)
            files = [path for path in (descriptor, source) if os.path.lexists(path)]
            if set(package.iterdir()) != {script, descriptor} or set(script.iterdir()) != ({source} if source in files else set()):
                raise WorkspaceError("unsafe_cleanup", "Временный пакет содержит чужие пути; остатки сохранены")
            for path in files:
                _assert_owned(created, path, stage)
            with _guard_directories(script, stage):
                for path in files:
                    _assert_owned(created, path, stage)
                    path.unlink()
            _assert_owned(created, script, stage, directory=True)
            script.rmdir()
            _assert_owned(created, package, stage, directory=True)
            package.rmdir()
    _assert_owned(created, stage, workspace, directory=True)
    stage.rmdir()


def prepare_probe(workspace_root: Path, *, repository_root: Path = REPOSITORY_ROOT) -> dict:
    """Атомарно подготовить новый пакет; существующий пакет не менять."""
    metadata, source = _fixture()
    workspace = _workspace_path(Path(workspace_root), Path(repository_root))
    repository = _checked(Path(repository_root), directory=True)
    target = workspace / PACKAGE_NAME
    stage = None
    created: dict[Path, tuple[int, int, str | None]] = {}
    with _destination_lock(target), _guard_directories(workspace, repository):
        if os.path.lexists(target):
            raise PackageError("already_exists", "Объектная fixture уже существует; выберите новую рабочую базу")
        try:
            stage = Path(tempfile.mkdtemp(prefix=".g1r-object-probe-", dir=workspace))
            _record_owned(created, stage, workspace, directory=True)
            with _guard_directories(stage, workspace):
                modkit = repository.parent / "G1R-Modkit"
                protected_roots = (modkit,) if modkit.is_dir() else ()
                staging_workspace = PackageWorkspace(stage, protected_roots=protected_roots)
                staging_workspace.create_package(PACKAGE_NAME, metadata)
                staged_package = stage / PACKAGE_NAME
                for directory in (staged_package, staged_package / "Script"):
                    _record_owned(created, directory, stage, directory=True)
                _record_owned(created, staged_package / "GothicMod.json", stage)
                staging_workspace.write_package_file(PACKAGE_NAME, SCRIPT_RELATIVE, source.decode("utf-8"))
                _record_owned(created, staged_package / SCRIPT_RELATIVE, stage)
                staged_package = _checked(stage / PACKAGE_NAME, stage, directory=True)
                validation = validate_package(stage, PACKAGE_NAME)
                if not validation["valid_structure"] or validation["script_count"] != 1:
                    raise PackageError("fixture_invalid", "Подготовленная копия fixture не прошла проверку")
                if os.path.lexists(target):
                    raise PackageError("already_exists", "Пакет появился до завершения подготовки")
                staged_package.rename(target)
        finally:
            if stage is not None:
                failure = sys.exc_info()[1]
                try:
                    _cleanup_stage(stage, workspace, created)
                except (WorkspaceError, OSError) as cleanup_error:
                    message = "Небезопасная очистка пропущена; временные остатки сохранены: " + str(cleanup_error)
                    if failure is None:
                        raise WorkspaceError("unsafe_cleanup", message) from cleanup_error
                    failure.add_note(message)

    validation = validate_package(workspace, PACKAGE_NAME)
    files = {}
    for relative_path in ("GothicMod.json", SCRIPT_RELATIVE):
        raw = (target / relative_path).read_bytes()
        files[relative_path] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return {
        "package_name": PACKAGE_NAME, "workspace_root": str(workspace), "package_path": str(target),
        "valid_structure": validation["valid_structure"], "script_count": validation["script_count"],
        "files": files, "compiled": False, "runtime_validation": "not_performed",
        "game_launched": False, "dll_loaded": False,
        "launch_options": [f"-ModsDirectory={workspace.as_posix()}", f"-Mods={PACKAGE_NAME}"],
        "startup_script": str(REPOSITORY_ROOT / "tools" / "start_g1r_development.ps1"),
        "startup_parameters": {"ModsDirectory": str(workspace), "Mods": [PACKAGE_NAME]},
        "expected": _expected(),
        "verification_recipe": [
            "Включить только этот пакет разовыми development-аргументами и подключить DLL после главного меню",
            "Найти три CDO и подтвердить class ancestry и реальные UFunction/types через reflection",
            "Вызвать ReadMarker, затем ReadSelf и проверить актуальный session-owned handle",
            "Проверить EchoObject с handle и null, IsNullObject с обоими вариантами",
            "Проверить EchoTypedObject с базовым/производным CDO; посторонний CDO должен отклоняться до выполнения",
            "Явно записать только StoredObject/StoredTypedObject проверочного CDO и сверить snapshot, затем вернуть null",
            "После закрытия сессии проверить отказ от старых handles до выполнения метода",
        ],
        "limitations": [
            "Исходные типы и ожидаемые имена не доказывают runtime reflection или компиляцию",
            "Пакет не содержит callbacks, subsystem, создания игровых объектов или работы с сохранениями",
            "Поля меняются только явными вызовами на собственных проверочных CDO",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Подготовить объектную fixture только внутри artifacts")
    parser.add_argument("--workspace-root", type=Path,
                        default=REPOSITORY_ROOT / "artifacts" / "object-probe-dev4")
    arguments = parser.parse_args(argv)
    try:
        result = prepare_probe(arguments.workspace_root)
    except (PackageError, WorkspaceError, OSError) as error:
        print(json.dumps({"code": getattr(error, "code", "access_error"),
                          "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
