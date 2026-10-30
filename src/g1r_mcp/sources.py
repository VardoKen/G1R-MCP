"""Ограниченный read-only каталог официальных AngelScript-исходников.

Ревизия — метка вызывающего сервера, а не самостоятельно проверенный Git.
Лексический список типов не заменяет компилятор и не описывает native bindings.
Обход последовательный: результат не является атомарным снимком всего Modkit.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 100_000
MAX_QUERY_CHARS = 512
MAX_READ_BYTES = 64 * 1024


class SourceError(Exception):
    """Предсказуемая ошибка источника без пользовательских абсолютных путей."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative: str
    root_kind: str


def _check_path(path: Path, boundary: Path | None = None, *, directory: bool = False) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        parts = (absolute, *absolute.parents)
        for part in parts:
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise SourceError("unsafe_path", "Путь источника содержит символическую ссылку или junction")
            if boundary is not None and part == boundary:
                break
        resolved = absolute.resolve(strict=True)
        if boundary is not None and not resolved.is_relative_to(boundary):
            raise SourceError("unsafe_path", "Путь источника выходит за разрешённый каталог")
        if directory and not resolved.is_dir():
            raise SourceError("invalid_path", "Каталог исходников имеет неподходящий тип")
        if not directory and not resolved.is_file():
            raise SourceError("invalid_path", "Источник имеет неподходящий тип")
        return resolved
    except SourceError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise SourceError("source_unavailable", "Источник отсутствует или недоступен") from None


def _read_text(file: SourceFile, boundary: Path) -> str:
    try:
        path = _check_path(file.path, boundary)
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise SourceError("file_too_large", "Исходник превышает предел 2 МиБ")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise SourceError("file_too_large", "Исходник превышает предел 2 МиБ")
        _check_path(path, boundary)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise SourceError("source_changed", "Исходник изменился во время чтения; повторите запрос")
        return raw.decode("utf-8-sig", errors="strict")
    except SourceError:
        raise
    except UnicodeDecodeError:
        raise SourceError("invalid_encoding", "Исходник не является корректным UTF-8") from None
    except (OSError, ValueError):
        raise SourceError("source_unavailable", "Источник отсутствует или недоступен") from None


def _discovered_file(path: Path, boundary: Path) -> Path:
    """Проверить файл в уже проверенном os.walk-каталоге без обхода предков."""
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise SourceError("unsafe_path", "Источник содержит символическую ссылку или junction")
        if not path.is_relative_to(boundary):
            raise SourceError("unsafe_path", "Источник выходит за разрешённый каталог")
        if not path.is_file():
            raise SourceError("invalid_path", "Источник имеет неподходящий тип")
        return path
    except SourceError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise SourceError("source_unavailable", "Источник отсутствует или недоступен") from None


def _limit(value: int, maximum: int = 200) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceError("invalid_arguments", f"Предел должен быть целым числом от 1 до {maximum}")
    return value


def _query(value: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > MAX_QUERY_CHARS or "\0" in value or (not empty and not value):
        raise SourceError("invalid_arguments", "Запрос должен быть непустой строкой длиной не более 512 символов")
    return value


def _mask_noncode(text: str) -> str:
    """Сохранить позиции/строки, исключив комментарии и строковые литералы."""
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda match: "".join("\n" if c == "\n" else " " for c in match.group()), text)


def _types(text: str) -> Iterator[dict]:
    """Извлечь объявления class/struct/enum с best-effort namespace."""
    masked = _mask_noncode(text)
    tokens = re.finditer(r"\bnamespace\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\{|\b(class|struct|enum)\s+([A-Za-z_]\w*)|[{}]", masked)
    namespace_stack: list[tuple[int, str]] = []
    depth = 0
    line_number = 1
    previous_offset = 0
    for token in tokens:
        line_number += masked.count("\n", previous_offset, token.start())
        previous_offset = token.start()
        if token.group(1):
            depth += 1
            namespace_stack.append((depth, token.group(1)))
        elif token.group(2):
            name = token.group(3)
            namespace = "::".join(item[1] for item in namespace_stack)
            yield {"kind": token.group(2), "name": name,
                   "qualified_name": f"{namespace}::{name}" if namespace else name,
                   "line": line_number}
        elif token.group() == "{":
            depth += 1
        else:
            if namespace_stack and namespace_stack[-1][0] == depth:
                namespace_stack.pop()
            depth = max(0, depth - 1)


class SourceCatalog:
    """Разрешённые Script/ и Examples/<пример>/Script/ явно заданного Modkit."""

    def __init__(self, modkit_root: str | Path, *, revision: str | None = None):
        self.root = _check_path(Path(modkit_root), directory=True)
        self.script_root = _check_path(self.root / "Script", self.root, directory=True)
        if revision is not None and (not isinstance(revision, str) or len(revision) > 256 or any(ord(c) < 32 for c in revision)):
            raise SourceError("invalid_arguments", "Метка ревизии имеет неподдерживаемый формат")
        self.revision = revision

    def _files(self) -> list[SourceFile]:
        _check_path(self.root, directory=True)
        roots = [(self.script_root, "game_script")]
        examples = self.root / "Examples"
        if examples.exists() or examples.is_symlink():
            examples = _check_path(examples, self.root, directory=True)
            try:
                for example in sorted(examples.iterdir(), key=lambda p: p.name.casefold()):
                    if example.is_symlink() or (hasattr(example, "is_junction") and example.is_junction()):
                        raise SourceError("unsafe_path", "Каталог примера содержит символическую ссылку или junction")
                    example_script = example / "Script"
                    if example.is_dir() and (example_script.exists() or example_script.is_symlink()):
                        roots.append((_check_path(example / "Script", self.root, directory=True), "example_script"))
            except SourceError:
                raise
            except OSError:
                raise SourceError("source_unavailable", "Каталог примеров недоступен") from None
        result: list[SourceFile] = []
        def walk_error(error: OSError) -> None:
            raise SourceError("source_unavailable", "Каталог исходников недоступен")
        for source_root, kind in roots:
            for directory, dirs, names in os.walk(source_root, followlinks=False, onerror=walk_error):
                parent = _check_path(Path(directory), self.root, directory=True)
                for name in dirs:
                    _check_path(parent / name, self.root, directory=True)
                for name in names:
                    if Path(name).suffix.casefold() != ".as":
                        continue
                    path = _discovered_file(parent / name, self.root)
                    result.append(SourceFile(path, path.relative_to(self.root).as_posix(), kind))
                    if len(result) > MAX_FILES:
                        raise SourceError("limit_exceeded", "Превышен предел количества исходников")
        if not result:
            raise SourceError("no_sources", "В разрешённых каталогах не найдены исходники AngelScript")
        return sorted(result, key=lambda file: file.relative)

    def _result(self, items: list[dict], total: int, scanned: int, **extra) -> dict:
        return {"items": items, "truncated": total > len(items), "truncation_scope": "items", "total_matches": total,
                "scanned_files": scanned, "scan_complete": True,
                "source": "official_modkit_static", "validation": "source_only",
                "revision": self.revision, "revision_source": "caller_label",
                "snapshot_kind": "sequential_file_reads", **extra}

    def _scan_result(self, items: list[dict], total: int, files: list[SourceFile], skipped: list[dict], **extra) -> dict:
        result = self._result(items, total, len(files), **extra)
        result.update(skipped_files=skipped[:100], skipped_count=len(skipped), skipped_list_truncated=len(skipped) > 100)
        if skipped:
            result["scan_complete"] = False
            result["total_matches"] = None
            result["total_matches_lower_bound"] = total
        return result

    @staticmethod
    def _item(file: SourceFile, **values) -> dict:
        return {"path": file.relative, "root_kind": file.root_kind, **values}

    def search_files(self, query: str, limit: int = 50) -> dict:
        needle = _query(query).casefold()
        _limit(limit)
        files = self._files()
        total = 0
        items = []
        for file in files:
            if needle in file.relative.casefold():
                total += 1
                if len(items) < limit:
                    items.append(self._item(file))
        return self._result(items, total, len(files))

    def search_text(self, query: str, limit: int = 50, case_sensitive: bool = False) -> dict:
        query = _query(query)
        _limit(limit)
        if not isinstance(case_sensitive, bool):
            raise SourceError("invalid_arguments", "Параметр регистра должен быть логическим значением")
        needle = query if case_sensitive else query.casefold()
        files = self._files()
        items = []
        total = 0
        skipped = []
        for file in files:
            try:
                text = _read_text(file, self.root)
            except SourceError as error:
                if error.code == "unsafe_path":
                    raise
                skipped.append(self._item(file, error_code=error.code))
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if needle in (line if case_sensitive else line.casefold()):
                    total += 1
                    if len(items) < limit:
                        items.append(self._item(file, line=line_number, text=line[:4096], text_truncated=len(line) > 4096))
        return self._scan_result(items, total, files, skipped, query_kind="literal")

    def read_file(self, relative_path: str, start_line: int = 1, line_count: int = 100) -> dict:
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path or "\0" in relative_path:
            raise SourceError("unsafe_path", "Нужен относительный путь с прямыми разделителями")
        path = PurePosixPath(relative_path)
        winpath = PureWindowsPath(relative_path)
        if path.is_absolute() or winpath.drive or winpath.root or any(part in (".", "..") for part in relative_path.split("/")):
            raise SourceError("unsafe_path", "Абсолютный путь или выход за каталог исходников запрещён")
        if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
            raise SourceError("invalid_arguments", "Номер первой строки должен быть положительным целым числом")
        _limit(line_count, 400)
        normalized = path.as_posix()
        parts = path.parts
        game_script = len(parts) >= 2 and parts[0] == "Script"
        example_script = len(parts) >= 4 and parts[0] == "Examples" and parts[2] == "Script"
        if path.suffix.casefold() != ".as" or not (game_script or example_script):
            raise SourceError("source_not_found", "Файл отсутствует в разрешённом каталоге AngelScript")
        _check_path(self.root, directory=True)
        try:
            source_path = _check_path(self.root / normalized, self.root)
        except SourceError as error:
            if error.code == "source_unavailable":
                raise SourceError("source_not_found", "Файл отсутствует в разрешённом каталоге AngelScript") from None
            raise
        file = SourceFile(source_path, normalized, "game_script" if game_script else "example_script")
        lines = _read_text(file, self.root).splitlines()
        selected = lines[start_line - 1:start_line - 1 + line_count]
        returned = []
        remaining = MAX_READ_BYTES
        byte_truncated = False
        last_line_truncated = False
        for line in selected:
            separator_bytes = 1 if returned else 0
            if remaining <= separator_bytes:
                byte_truncated = True
                break
            encoded = line.encode("utf-8")
            if len(encoded) + separator_bytes > remaining:
                returned.append(encoded[:remaining - separator_bytes].decode("utf-8", errors="ignore"))
                byte_truncated = last_line_truncated = True
                break
            returned.append(line)
            remaining -= len(encoded) + separator_bytes
        text = "\n".join(returned)
        item = self._item(file, start_line=start_line, end_line=start_line + len(returned) - 1 if returned else None,
                          total_lines=len(lines), text=text, returned_lines=len(returned),
                          returned_utf8_bytes=len(text.encode("utf-8")), max_text_bytes=MAX_READ_BYTES,
                          last_line_truncated=last_line_truncated,
                          content_truncated=byte_truncated or start_line > 1 or start_line - 1 + line_count < len(lines))
        return self._result([item], 1, 1, **item)

    def list_types(self, query: str = "", limit: int = 50) -> dict:
        needle = _query(query, empty=True).casefold()
        _limit(limit)
        files = self._files()
        items = []
        total = 0
        skipped = []
        for file in files:
            try:
                text = _read_text(file, self.root)
            except SourceError as error:
                if error.code == "unsafe_path":
                    raise
                skipped.append(self._item(file, error_code=error.code))
                continue
            for declaration in _types(text):
                if needle in declaration["qualified_name"].casefold():
                    total += 1
                    if len(items) < limit:
                        items.append(self._item(file, **declaration))
        return self._scan_result(items, total, files, skipped, parser_kind="lexical",
                            limitations=["Неполный лексический индекс, не AST компилятора", "Native bindings не включены", "Вызовы в игре не проверены"])
