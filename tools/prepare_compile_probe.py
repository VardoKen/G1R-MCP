"""Подготовить изолированный AngelScript-пакет и рецепт; игру не запускать.

Источник синтаксиса: официальный G1R-Modkit, Script/Camera/VisionSettings.as
(UCLASS/UObject) и Script/AI/States/FightAI/CombatState/CombatMoves.as
(UFUNCTION(BlueprintCallable)). Компиляция fixture этим не подтверждается.
После включения пакета явная подготовка reload может инициировать hot reload игры.
"""

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
from g1r_mcp.workspace import PackageWorkspace, WorkspaceError, _checked, _destination_lock, _guard_directories, _read_bytes

PACKAGE_NAME = "G1RMCPProbe"
CLASS_NAME = "UG1RMCPCompileProbe"
METHOD_NAME = "ReadProbeValue"
SCRIPT_RELATIVE = "Script/G1RMCPProbe.as"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "angelscript_probe"


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _cleanup_own_stage(stage: Path, workspace: Path, files: dict, directories: dict) -> None:
    """Удалить только свои неизменённые файлы; подозрительное дерево сохранить."""
    try:
        with _guard_directories(stage, workspace):
            for relative, identity in directories.items():
                path = _checked(stage if relative == "." else stage / relative, stage, directory=True)
                if _file_identity(path) != identity:
                    return
            for relative in files:
                _checked((stage / relative).parent, stage, directory=True)
            for relative, (identity, digest) in files.items():
                path = stage / relative
                with _guard_directories(path.parent, workspace):
                    checked = _checked(path, stage)
                    if _file_identity(checked) == identity and _hash(_read_bytes(checked, stage, 2 * 1024 * 1024)) == digest:
                        checked.unlink()
            # Guard stage остаётся активным, Script guard уже закрыт.
            for relative, identity in reversed(tuple(directories.items())):
                if relative == ".":
                    continue
                path = _checked(stage / relative, stage, directory=True)
                if _file_identity(path) == identity and not any(path.iterdir()):
                    path.rmdir()
        checked_stage = _checked(stage, workspace, directory=True)
        if _file_identity(checked_stage) == directories.get(".") and not any(checked_stage.iterdir()):
            checked_stage.rmdir()
    except (WorkspaceError, OSError):
        pass


def _checked_artifacts_path(path: Path, repository_root: Path) -> Path:
    """Проверить абсолютную цель внутри artifacts и отсутствие ссылок."""
    absolute = Path(os.path.abspath(path))
    repository = Path(os.path.abspath(repository_root))
    boundary = repository / "artifacts"
    if absolute == boundary or not absolute.is_relative_to(boundary):
        raise PackageError("unsafe_path", "Выберите отдельный каталог внутри artifacts подпроекта")
    for component in (absolute, *absolute.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise PackageError("unsafe_path", "Путь содержит ссылку или junction")
        if component.exists() and not component.is_dir():
            raise PackageError("invalid_path", "Родительский путь должен быть каталогом")
    if not absolute.resolve().is_relative_to(boundary.resolve()):
        raise PackageError("unsafe_path", "Разрешённый путь вышел за пределы artifacts")
    return absolute


def _fixture_bytes() -> tuple[bytes, bytes]:
    checked = validate_package(FIXTURE_ROOT, ".")
    if not checked["valid_structure"] or checked["script_count"] != 1:
        raise PackageError("fixture_invalid", "Проверочный fixture имеет неподходящую структуру")
    descriptor = (FIXTURE_ROOT / "GothicMod.json").read_bytes()
    source = (FIXTURE_ROOT / SCRIPT_RELATIVE).read_bytes()
    if checked["metadata"].get("name") != PACKAGE_NAME or source.count(b"return 1;") != 1:
        raise PackageError("fixture_invalid", "Проверочный fixture не соответствует рецепту")
    return descriptor, source


def prepare_probe(workspace_root: Path, *, phase: str = "baseline",
                  expected_sha256: str | None = None,
                  repository_root: Path = REPOSITORY_ROOT) -> dict:
    """Подготовить baseline или заменить единственную константу при reload.

    Операция не открывает игру, не загружает DLL и не устанавливает пакет.
    Существующий baseline не перезаписывается. Reload требует точного
    хеша baseline и проверяет оба файла перед изменением тела функции.
    """
    if phase not in {"baseline", "reload"}:
        raise PackageError("invalid_phase", "Допустимы фазы baseline и reload")
    if phase == "baseline" and expected_sha256 is not None:
        raise PackageError("invalid_hash", "Baseline создаётся без ожидаемого хеша")
    if phase == "reload" and (not isinstance(expected_sha256, str)
                               or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise PackageError("invalid_hash", "Reload требует SHA-256 baseline из результата подготовки")
    workspace = _checked_artifacts_path(workspace_root, repository_root)
    package = _checked_artifacts_path(workspace / PACKAGE_NAME, repository_root)
    descriptor, baseline = _fixture_bytes()
    source = baseline if phase == "baseline" else baseline.replace(b"return 1;", b"return 2;", 1)
    if phase == "baseline":
        if package.exists():
            raise PackageError("already_exists", "Проверочный пакет уже существует; выберите новый каталог")
        workspace.mkdir(parents=True, exist_ok=True)
        _checked_artifacts_path(workspace, repository_root)
        stage = None
        owned_files = {}
        owned_directories = {}
        with _destination_lock(package), _guard_directories(workspace, Path(repository_root)):
            try:
                if package.exists():
                    raise PackageError("already_exists", "Проверочный пакет уже существует")
                stage = Path(tempfile.mkdtemp(prefix=".g1r-mcp-probe-", dir=workspace))
                _checked_artifacts_path(stage, repository_root)
                owned_directories["."] = _file_identity(stage)
                with _guard_directories(stage, workspace):
                    (stage / "Script").mkdir()
                    owned_directories["Script"] = _file_identity(stage / "Script")
                    writer = PackageWorkspace(workspace)
                    with _guard_directories(stage / "Script", stage):
                        for relative, raw in (("GothicMod.json", descriptor), (SCRIPT_RELATIVE, source)):
                            result = writer.write_package_file(stage.name, relative, raw.decode("utf-8"))
                            owned_files[relative] = (_file_identity(_checked(stage / relative, stage)), result["sha256"])
                        validation = validate_package(workspace, stage.name)
                        if not validation["valid_structure"] or validation["script_count"] != 1:
                            raise PackageError("fixture_invalid", "Копия fixture не прошла структурную проверку")
                        if package.exists():
                            raise PackageError("already_exists", "Проверочный пакет появился до завершения подготовки")
                    _checked(stage / "Script", workspace, directory=True)
                stage.rename(package)
                stage = None
            finally:
                if stage is not None:
                    _cleanup_own_stage(stage, workspace, owned_files, owned_directories)
    else:
        if not package.is_dir():
            raise PackageError("invalid_path", "Пакет baseline не найден")
        before = validate_package(workspace, PACKAGE_NAME)
        if not before["valid_structure"] or before["script_count"] != 1:
            raise PackageError("fixture_changed", "Структура подготовленного пакета изменилась")
        actual_files = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}
        actual_directories = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_dir()}
        if actual_files != {"GothicMod.json", SCRIPT_RELATIVE} or actual_directories != {"Script"}:
            raise PackageError("fixture_changed", "Проверочный пакет содержит дополнительные файлы или каталоги")
        source_path = package / SCRIPT_RELATIVE
        current = source_path.read_bytes()
        if current != baseline or _hash(current) != expected_sha256:
            raise PackageError("file_changed", "Исходник отличается от baseline или ожидаемого хеша")
        if (package / "GothicMod.json").read_bytes() != descriptor:
            raise PackageError("fixture_changed", "Дескриптор проверочного пакета изменился")
        try:
            PackageWorkspace(workspace).write_package_file(PACKAGE_NAME, SCRIPT_RELATIVE,
                                                           source.decode("utf-8"), expected_sha256)
        except WorkspaceError as error:
            raise PackageError(error.code, str(error)) from None
    verified = validate_package(workspace, PACKAGE_NAME)
    return {
        "phase": phase,
        "workspace_root": str(workspace),
        "package_root": str(package),
        "package_name": PACKAGE_NAME,
        "valid_structure": verified["valid_structure"],
        "script_count": verified["script_count"],
        "files": {
            "GothicMod.json": {"sha256": _hash(descriptor), "bytes": len(descriptor)},
            SCRIPT_RELATIVE: {"sha256": _hash(source), "bytes": len(source)},
        },
        "game_launched": False,
        "dll_loaded": False,
        "runtime_validation": "not_performed",
        "compiled": False,
        "reloaded": False,
        "launch_options": [f"-ModsDirectory={workspace.as_posix()}", f"-Mods={PACKAGE_NAME}"],
        "launch_options_effect": "В текущей сессии выбран только проверочный пакет из внешнего каталога",
        "existing_launcher": str(REPOSITORY_ROOT / "tools" / "start_g1r_development.ps1"),
        "launcher_accepts_package_options": True,
        "launcher_parameters": {"ModsDirectory": str(workspace), "Mods": [PACKAGE_NAME]},
        "expected": {
            "class_name": CLASS_NAME,
            "object_name": "Default__G1RMCPCompileProbe",
            "function_name": METHOD_NAME,
            "signature": {"input_parameters": [], "return_type": "IntProperty", "return_bytes": 4},
            "value": 1 if phase == "baseline" else 2,
        },
        "verification_recipe": [
            "Включить пакет указанными launch_options при development-запуске через Steam",
            "После главного меню подключить существующую DLL и подтвердить ready через g1r_native_status",
            "Найти точный CDO по ожидаемому object_name и классу; отсутствие означает непроверенную загрузку",
            "Прочитать g1r_object_functions и перед вызовом сверить имя, отсутствие входов и IntProperty возврат размером 4",
            "Вызвать только ReadProbeValue с пустыми arguments; сравнить результат с expected.value",
            "Для явной фазы reload заменить только return 1 на return 2 с хешем baseline и повторить проверку сигнатуры/вызов",
        ],
        "limitations": [
            "Структурная проверка и хеш не доказывают компиляцию или загрузку пакета",
            "Ненулевой выход игры без диагностики не отличает compiler error от другого сбоя",
            "Структурированная диагностика компиляции и debugger текущей сборки не подтверждены",
            "После включения пакета запись reload может автоматически инициировать горячую перезагрузку",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Подготовить проверочный пакет в artifacts, не запуская игру")
    parser.add_argument("--workspace-root", required=True, type=Path, help="Отдельный каталог внутри artifacts подпроекта")
    parser.add_argument("--phase", choices=("baseline", "reload"), default="baseline", help="Создать baseline или изменить только тело функции")
    parser.add_argument("--expected-sha256", help="SHA-256 исходника baseline, обязательный для reload")
    arguments = parser.parse_args(argv)
    try:
        result = prepare_probe(arguments.workspace_root, phase=arguments.phase,
                               expected_sha256=arguments.expected_sha256)
    except (PackageError, WorkspaceError, OSError) as error:
        print(json.dumps({"code": getattr(error, "code", "access_error"), "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
