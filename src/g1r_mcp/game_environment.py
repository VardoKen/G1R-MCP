"""Сверка явно указанной среды игры и Modkit без изменения файлов.

Читаются только Steam-манифест, Script/ официального Modkit и G1R/Script/
игры; по отдельному флагу — известный игровой EXE. Сохранения, настройки
и соседние каталоги не исследуются. Все диагностические ошибки очищены
от содержимого манифеста и пользовательских путей.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re

from .source_identity import inspect as inspect_source_identity


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MANIFEST_TOKENS = 100_000
MAX_MANIFEST_DEPTH = 16
MAX_SOURCE_FILES = 200_000
HASH_CHUNK_BYTES = 1024 * 1024


class InspectionError(Exception):
    """Предсказуемая ошибка с безопасным кодом и русским описанием."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InspectionOptions:
    game_root: Path
    modkit_root: Path
    steam_manifest: Path
    hash_executable: bool = False
    max_list_entries: int | None = 100


def _invalid_manifest() -> InspectionError:
    return InspectionError("invalid_manifest", "Steam-манифест повреждён или имеет неподдерживаемую структуру")


def _tokens(text: str) -> list[str | tuple[str, str]]:
    """Строго разобрать кавычки, экранирование, скобки и комментарии ACF."""
    tokens: list[str | tuple[str, str]] = []
    offset = 0
    escapes = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    while offset < len(text):
        char = text[offset]
        if char.isspace():
            offset += 1
            continue
        if text.startswith("//", offset):
            newline = text.find("\n", offset)
            offset = len(text) if newline < 0 else newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            offset += 1
        elif char == '"':
            offset += 1
            value: list[str] = []
            while offset < len(text) and text[offset] != '"':
                char = text[offset]
                if char == "\\":
                    offset += 1
                    if offset >= len(text) or text[offset] not in escapes:
                        raise _invalid_manifest()
                    value.append(escapes[text[offset]])
                else:
                    if ord(char) < 32:
                        raise _invalid_manifest()
                    value.append(char)
                offset += 1
            if offset >= len(text):
                raise _invalid_manifest()
            tokens.append(("string", "".join(value)))
            offset += 1
        else:
            raise _invalid_manifest()
        if len(tokens) > MAX_MANIFEST_TOKENS:
            raise _invalid_manifest()
    return tokens


def parse_manifest(text: str) -> dict[str, str]:
    """Вернуть только разрешённые метаданные; остальное не попадает в ответ."""
    tokens = _tokens(text.lstrip("\ufeff"))
    offset = 0

    def parse_object(depth: int, nested: bool) -> dict:
        nonlocal offset
        if depth > MAX_MANIFEST_DEPTH:
            raise _invalid_manifest()
        result: dict = {}
        while offset < len(tokens):
            token = tokens[offset]
            if token == "}":
                if not nested:
                    raise _invalid_manifest()
                offset += 1
                return result
            if not isinstance(token, tuple):
                raise _invalid_manifest()
            key = token[1].casefold()
            if key in result:
                raise _invalid_manifest()
            offset += 1
            if offset >= len(tokens):
                raise _invalid_manifest()
            token = tokens[offset]
            offset += 1
            if token == "{":
                result[key] = parse_object(depth + 1, True)
            elif isinstance(token, tuple):
                result[key] = token[1]
            else:
                raise _invalid_manifest()
        if nested:
            raise _invalid_manifest()
        return result

    tree = parse_object(0, False)
    if set(tree) != {"appstate"} or not isinstance(tree["appstate"], dict):
        raise _invalid_manifest()
    state = tree["appstate"]
    selected: dict[str, str] = {}
    for key in ("appid", "buildid", "installdir", "lastupdated"):
        value = state.get(key)
        if value is None and key == "lastupdated":
            continue
        if not isinstance(value, str):
            raise _invalid_manifest()
        selected["LastUpdated" if key == "lastupdated" else key] = value
    for key in ("appid", "buildid", "LastUpdated"):
        if key in selected and (not re.fullmatch(r"[0-9]+", selected[key]) or len(selected[key]) > 20):
            raise _invalid_manifest()
    if int(selected["appid"]) < 1 or int(selected["buildid"]) < 1:
        raise _invalid_manifest()
    install_dir = selected["installdir"]
    if not install_dir or install_dir in (".", "..") or any(char in install_dir for char in "/\\:\0"):
        raise _invalid_manifest()
    if any(ord(char) < 32 for char in install_dir):
        raise _invalid_manifest()
    user_config = state.get("userconfig", {})
    if not isinstance(user_config, dict):
        raise _invalid_manifest()
    beta = user_config.get("betakey")
    if beta is not None:
        if not isinstance(beta, str) or len(beta) > 256 or any(ord(char) < 32 for char in beta):
            raise _invalid_manifest()
        selected["betakey"] = beta
    return selected


def _safe_path(path: Path, kind: str, boundary: Path | None = None) -> Path:
    """Отклонить ссылки/junctions, отсутствие и выход из выбранного корня."""
    try:
        absolute = Path(os.path.abspath(path))
        for component in (absolute, *absolute.parents):
            if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
                raise InspectionError("unsafe_path", "Путь содержит символическую ссылку или junction")
        resolved = absolute.resolve(strict=True)
        if boundary is not None and not resolved.is_relative_to(boundary):
            raise InspectionError("unsafe_path", "Путь выходит за пределы выбранного корня")
        if (kind == "directory" and not resolved.is_dir()) or (kind == "file" and not resolved.is_file()):
            raise InspectionError("invalid_path", "Указанный путь имеет неподходящий тип")
        return resolved
    except InspectionError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise InspectionError("invalid_path", "Указанный путь отсутствует или недоступен") from None


def _hash_file(path: Path, boundary: Path) -> tuple[str, int]:
    """Посчитать SHA-256 потоково и обнаружить изменение файла при чтении."""
    path = _safe_path(path, "file", boundary)
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        _safe_path(path, "file", boundary)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise InspectionError("file_changed", "Проверяемый файл изменился во время чтения")
        return digest.hexdigest(), after.st_size
    except InspectionError:
        raise
    except OSError:
        raise InspectionError("read_error", "Не удалось прочитать проверяемый файл") from None


def _source_files(root: Path) -> list[Path]:
    """Обходить только Script/ без следования ссылкам и с пределом файлов."""
    result: list[Path] = []

    def fail_walk(error: OSError) -> None:
        raise InspectionError("read_error", "Не удалось прочитать каталог скриптов")

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        parent = _safe_path(Path(directory), "directory", root)
        for name in dirs:
            _safe_path(parent / name, "directory", root)
        for name in files:
            if Path(name).suffix.casefold() != ".as":
                continue
            result.append(_safe_path(parent / name, "file", root))
            if len(result) > MAX_SOURCE_FILES:
                raise InspectionError("limit_exceeded", "Превышен диагностический предел количества скриптов")
    if not result:
        raise InspectionError("no_source_files", "В Script/ Modkit не найдено файлов AngelScript")
    return sorted(result, key=lambda file: file.relative_to(root).as_posix())


def inspect_environment(options: InspectionOptions) -> dict:
    """Сверить три явно заданных источника и вернуть безопасный JSON-результат."""
    if options.max_list_entries is not None and not 0 <= options.max_list_entries <= 10_000:
        raise InspectionError("invalid_arguments", "Предел списков должен быть от 0 до 10000")
    game_root = _safe_path(options.game_root, "directory")
    modkit_root = _safe_path(options.modkit_root, "directory")
    manifest = _safe_path(options.steam_manifest, "file")
    try:
        with manifest.open("rb") as stream:
            raw_manifest = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise InspectionError("limit_exceeded", "Steam-манифест превышает диагностический предел размера")
        metadata = parse_manifest(raw_manifest.decode("utf-8-sig", errors="strict"))
    except InspectionError:
        raise
    except (OSError, UnicodeDecodeError):
        raise _invalid_manifest() from None
    if metadata["installdir"].casefold() != game_root.name.casefold():
        raise InspectionError("manifest_game_mismatch", "Steam-манифест относится к другому установочному каталогу")

    source_root = _safe_path(modkit_root / "Script", "directory", modkit_root)
    game_script_root = _safe_path(game_root / "G1R" / "Script", "directory", game_root)
    sources = _source_files(source_root)
    compared = identical = different = missing = 0
    different_files: list[str] = []
    missing_files: list[str] = []
    for source in sources:
        relative = source.relative_to(source_root)
        target = game_script_root / relative
        # Сначала проверяются ссылки: dangling symlink нельзя считать отсутствием.
        absolute = Path(os.path.abspath(target))
        for component in (absolute, *absolute.parents):
            if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
                raise InspectionError("unsafe_path", "Путь содержит символическую ссылку или junction")
        if not target.exists():
            missing += 1
            if options.max_list_entries is None or len(missing_files) < options.max_list_entries:
                missing_files.append(relative.as_posix())
            continue
        source_hash, _ = _hash_file(source, source_root)
        target_hash, _ = _hash_file(target, game_script_root)
        compared += 1
        if source_hash == target_hash:
            identical += 1
        else:
            different += 1
            if options.max_list_entries is None or len(different_files) < options.max_list_entries:
                different_files.append(relative.as_posix())

    result = {
        "ok": True,
        "manifest": metadata,
        "scripts": {
            "source_count": len(sources), "compared": compared, "identical": identical,
            "different": different, "missing": missing,
            "different_files": different_files, "missing_files": missing_files,
            "max_list_entries": options.max_list_entries,
            "lists_truncated": different > len(different_files) or missing > len(missing_files),
        },
    }
    if options.hash_executable:
        executable = game_root / "G1R" / "Binaries" / "Win64" / "G1R-Win64-Shipping.exe"
        digest, size = _hash_file(executable, game_root)
        result["executable"] = {"sha256": digest, "size_bytes": size}
    return result


def verify_source_game(modkit_root: Path, game_root: Path, hash_executable: bool = False,
                       *, configured_revision: str | None = None) -> dict:
    """Сверить подмножество Script Modkit с установленными скриптами, без запуска игры."""
    started_at = datetime.now(timezone.utc).isoformat()
    source_identity = inspect_source_identity(modkit_root, configured_revision)
    result = inspect_environment(InspectionOptions(
        game_root=game_root,
        modkit_root=modkit_root,
        steam_manifest=game_root.parent.parent / "appmanifest_1297900.acf",
        hash_executable=hash_executable,
        max_list_entries=None,
    ))
    scripts = result["scripts"]
    result.update(
        inspection_started_at=started_at,
        inspection_finished_at=datetime.now(timezone.utc).isoformat(),
        snapshot_kind="sequential_file_reads",
        source_identity=source_identity,
        source_identity_observation="before_file_comparison",
        correlation_scope="modkit_script_subset_byte_equality",
        subset_matches_game_scripts=(scripts["different"] == 0 and scripts["missing"] == 0
                                     and scripts["compared"] == scripts["source_count"]),
        game_only_scripts_checked=False,
        runtime_compatibility_checked=False,
    )
    return result


