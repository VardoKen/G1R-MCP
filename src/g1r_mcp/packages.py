"""Локальная проверка официального пакета Gothic без компиляции и загрузки.

Формат сверён с G1R-Modkit/Documentation/getting-started/mod-package.md.
Валидатор читает GothicMod.json, считает .as внутри Script/ и .patch непосредственно
в ScriptPatches/. Содержимое патчей, Config, Content и Story не читается. Работа
с загрузчиком, применение патчей, совместимость версий и AngelScript не подтверждаются.
"""

import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat


MAX_DESCRIPTOR_BYTES = 256 * 1024
MAX_SCRIPT_FILES = 10_000
MAX_ISSUES = 100
COMPONENTS = ("Script", "Config", "Content", "Story", "ScriptPatches")
LOAD_ORDERS = {"first", "early", "normal", "late", "last"}
KNOWN_FIELDS = {
    "name", "title", "author", "description", "projectWebsiteURL",
    "modVersion", "dependencies", "loadOrder",
}


class PackageError(Exception):
    """Ошибка доступа или небезопасный путь; сообщение не раскрывает пути."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _checked_path(path: Path, boundary: Path | None = None) -> Path:
    """Отклонить ссылки, junction и выход из явно выбранной рабочей базы."""
    try:
        absolute = Path(os.path.abspath(path))
        for part in (absolute, *absolute.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PackageError("unsafe_path", "Путь содержит символическую ссылку или junction")
        resolved = absolute.resolve(strict=True)
        if boundary is not None and not resolved.is_relative_to(boundary):
            raise PackageError("unsafe_path", "Путь выходит за пределы рабочей базы")
        return resolved
    except PackageError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise PackageError("access_error", "Путь отсутствует или недоступен") from None


def _relative_package(workspace_root: Path, relative_path: str) -> tuple[Path, str]:
    if not isinstance(relative_path, str) or not relative_path.strip() or "\0" in relative_path:
        raise PackageError("invalid_path", "Нужен относительный путь пакета")
    if Path(relative_path).is_absolute() or PureWindowsPath(relative_path).anchor or ":" in relative_path:
        raise PackageError("unsafe_path", "Абсолютный путь пакета не допускается")
    pieces = re.split(r"[/\\]", relative_path)
    if ".." in pieces:
        raise PackageError("unsafe_path", "Переход к родительскому каталогу не допускается")
    pieces = [piece for piece in pieces if piece not in ("", ".")]
    root = _checked_path(Path(workspace_root))
    if not root.is_dir():
        raise PackageError("invalid_path", "Рабочая база должна быть каталогом")
    package = _checked_path(root.joinpath(*pieces), root)
    if not package.is_dir():
        raise PackageError("invalid_path", "Пакет должен быть каталогом")
    return package, "/".join(pieces) or "."


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON constant")


def _read_descriptor(path: Path, boundary: Path) -> tuple[object | None, str | None]:
    checked = _checked_path(path, boundary)
    if not checked.is_file():
        return None, "descriptor_not_file"
    try:
        before = checked.stat()
        with checked.open("rb") as stream:
            raw = stream.read(MAX_DESCRIPTOR_BYTES + 1)
        _checked_path(checked, boundary)
        after = checked.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise PackageError("file_changed", "Дескриптор изменился во время проверки")
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            return None, "descriptor_too_large"
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant), None
    except PackageError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "invalid_json"
    except OSError:
        raise PackageError("access_error", "Не удалось прочитать дескриптор") from None


def _script_count(script_root: Path, boundary: Path) -> int:
    total = 0

    def walk_error(error: OSError) -> None:
        raise PackageError("access_error", "Не удалось прочитать каталог скриптов")

    for directory, dirs, files in os.walk(script_root, followlinks=False, onerror=walk_error):
        parent = _checked_path(Path(directory), boundary)
        for name in dirs:
            _checked_path(parent / name, boundary)
        for name in files:
            if Path(name).suffix.casefold() != ".as":
                continue
            file = _checked_path(parent / name, boundary)
            if not file.is_file():
                raise PackageError("invalid_path", "Скрипт должен быть обычным файлом")
            total += 1
            if total > MAX_SCRIPT_FILES:
                raise PackageError("limit_exceeded", "Превышен диагностический предел количества скриптов")
    return total


def _patch_count(patch_root: Path, boundary: Path) -> int:
    """Считать документированные top-level .patch без анализа diff или UTF-8."""
    total = 0
    try:
        for candidate in patch_root.iterdir():
            file = _checked_path(candidate, boundary)
            if candidate.suffix.casefold() != ".patch":
                continue
            if not file.is_file():
                raise PackageError("invalid_path", "Патч должен быть обычным файлом")
            total += 1
            if total > MAX_SCRIPT_FILES:
                raise PackageError("limit_exceeded", "Превышен диагностический предел количества патчей")
    except PackageError:
        raise
    except OSError:
        raise PackageError("access_error", "Не удалось прочитать каталог патчей") from None
    return total


def validate_package(workspace_root: Path, relative_path: str) -> dict:
    """Проверить пакет только внутри выбранной рабочей базы без изменений."""
    package, normalized = _relative_package(workspace_root, relative_path)
    result = {
        "package_path": normalized,
        "valid_json": False,
        "valid_structure": False,
        "runtime_validation": "not_performed",
        "compiled": False,
        "metadata": {},
        "components": {},
        "script_count": 0,
        "patch_count": 0,
        "patch_validation": "not_performed",
        "errors": [],
        "warnings": [],
        "issues_truncated": False,
    }
    error_count = 0

    def issue(kind: str, code: str, message: str, field: str | None = None) -> None:
        nonlocal error_count
        if kind == "errors":
            error_count += 1
        if len(result[kind]) >= MAX_ISSUES:
            result["issues_truncated"] = True
            return
        item = {"code": code, "message": message}
        if field is not None:
            item["field"] = field[:128]
        result[kind].append(item)

    issue("warnings", "runtime_not_verified", "Компиляция, загрузка и сравнение версий игрой не проверялись")
    for component in COMPONENTS:
        path = package / component
        # Dangling symlink также должен быть отклонён, а не принят за отсутствие.
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise PackageError("unsafe_path", "Компонент пакета содержит ссылку или junction")
        present = path.exists()
        is_directory = False
        if present:
            checked = _checked_path(path, package)
            is_directory = checked.is_dir()
            if not is_directory:
                issue("errors", "component_not_directory", "Компонент пакета должен быть каталогом", component)
            elif component == "Script":
                result["script_count"] = _script_count(checked, package)
            elif component == "ScriptPatches":
                result["patch_count"] = _patch_count(checked, package)
        result["components"][component] = {"present": present, "is_directory": is_directory}
    if result["script_count"] == 0 and result["patch_count"] == 0:
        issue("warnings", "no_scripts", "В пакете нет новых AngelScript-файлов или документированных патчей; возможен пакет контента")

    descriptor_path = package / "GothicMod.json"
    if descriptor_path.is_symlink() or (hasattr(descriptor_path, "is_junction") and descriptor_path.is_junction()):
        raise PackageError("unsafe_path", "Дескриптор содержит ссылку или junction")
    if not descriptor_path.exists():
        issue("errors", "descriptor_missing", "В корне пакета отсутствует GothicMod.json")
        return result
    descriptor, failure = _read_descriptor(descriptor_path, package)
    if failure:
        messages = {
            "descriptor_not_file": "GothicMod.json должен быть обычным файлом",
            "descriptor_too_large": "GothicMod.json превышает предел 256 КиБ",
            "invalid_json": "GothicMod.json не является корректным UTF-8 JSON с уникальными ключами",
        }
        issue("errors", failure, messages[failure])
        return result
    result["valid_json"] = True
    if not isinstance(descriptor, dict):
        issue("errors", "descriptor_not_object", "Корнем GothicMod.json должен быть объект")
        return result

    for field in ("name", "title", "author", "modVersion"):
        value = descriptor.get(field)
        if not isinstance(value, str) or not value.strip():
            issue("errors", "required_string", "Обязательное поле должно быть непустой строкой", field)
        else:
            result["metadata"][field] = value
    for field in ("description", "projectWebsiteURL"):
        if field in descriptor:
            if not isinstance(descriptor[field], str):
                issue("errors", "invalid_field_type", "Необязательное поле должно быть строкой", field)
            else:
                result["metadata"][field] = descriptor[field]
    if "loadOrder" in descriptor:
        if not isinstance(descriptor["loadOrder"], str) or descriptor["loadOrder"] not in LOAD_ORDERS:
            issue("errors", "invalid_load_order", "loadOrder должен быть first, early, normal, late или last", "loadOrder")
        else:
            result["metadata"]["loadOrder"] = descriptor["loadOrder"]
    for field in sorted(descriptor.keys() - KNOWN_FIELDS):
        issue("warnings", "unknown_field", "Поле не описано проверенным руководством; его поддержка игрой неизвестна", field)
    if "dependencies" in descriptor:
        dependencies = descriptor["dependencies"]
        if not isinstance(dependencies, list):
            issue("errors", "invalid_dependencies", "dependencies должно быть массивом", "dependencies")
        else:
            valid_dependencies = []
            for index, dependency in enumerate(dependencies):
                prefix = f"dependencies[{index}]"
                if not isinstance(dependency, dict):
                    issue("errors", "invalid_dependency", "Зависимость должна быть объектом", prefix)
                    continue
                name, version = dependency.get("name"), dependency.get("requiredVersion")
                if not isinstance(name, str) or not name.strip() or not isinstance(version, str):
                    issue("errors", "invalid_dependency", "Зависимость требует непустое name и строковое requiredVersion", prefix)
                else:
                    valid_dependencies.append({"name": name, "requiredVersion": version})
                for field in sorted(dependency.keys() - {"name", "requiredVersion"}):
                    issue("warnings", "unknown_field", "Поле зависимости не описано проверенным руководством", f"{prefix}.{field}")
            result["metadata"]["dependencies"] = valid_dependencies
    result["valid_structure"] = error_count == 0
    return result
