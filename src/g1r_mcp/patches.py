"""Создать стандартный unified diff официального Script/ без записи исходников.

Хеши относятся к исходным байтам; переводы строк и UTF-8 BOM не нормализуются.
Стандартный формат diff не подтверждает применение игровым загрузчиком.
"""
from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
import re

from .workspace import WorkspaceError, _checked, _guard_directories, _parts, _read_bytes

MAX_SOURCE_BYTES = 1024 * 1024
MAX_PATCH_BYTES = 2 * 1024 * 1024


class PatchError(Exception):
    """Предсказуемая ошибка создания патча без раскрытия абсолютного пути."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ScriptPatchBuilder:
    def __init__(self, modkit_root: str | Path, revision: str | None = None):
        try:
            self.root = _checked(Path(modkit_root), directory=True)
            self.script_root = _checked(self.root / "Script", self.root, directory=True)
        except WorkspaceError as error:
            raise PatchError(error.code, str(error)) from None
        self.revision = revision

    def _source(self, source_path: str) -> tuple[str, bytes]:
        try:
            parts = _parts(source_path)
            if len(parts) < 2 or parts[0] != "Script" or Path(parts[-1]).suffix.casefold() != ".as":
                raise PatchError("unsupported_file", "Источником патча должен быть Script/**/*.as официального Modkit")
            path = _checked(self.root.joinpath(*parts), self.script_root)
            with _guard_directories(path.parent, self.script_root):
                raw = _read_bytes(path, self.script_root, MAX_SOURCE_BYTES)
        except WorkspaceError as error:
            raise PatchError(error.code, str(error)) from None
        self._text(raw)
        return "/".join(parts), raw

    @staticmethod
    def _text(raw: bytes) -> str:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PatchError("invalid_encoding", "Исходник и замена должны быть корректным UTF-8") from None
        if "\0" in text:
            raise PatchError("invalid_text", "Двоичное содержимое с NUL не поддерживается")
        if "\r" in text.replace("\r\n", ""):
            raise PatchError("unsupported_line_endings", "Поддерживаются LF и CRLF; одиночный CR не поддерживается")
        return text

    def source_info(self, source_path: str) -> dict:
        path, raw = self._source(source_path)
        return {"source": "official_modkit", "revision": self.revision, "path": path,
                "bytes": len(raw), "source_sha256": hashlib.sha256(raw).hexdigest(),
                "max_source_bytes": MAX_SOURCE_BYTES}

    def build_script_patch(self, source_path: str, replacement_content: str,
                           expected_source_sha256: str, context_lines: int = 3) -> dict:
        if (not isinstance(expected_source_sha256, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_source_sha256)):
            raise PatchError("invalid_arguments", "Нужен SHA-256 исходника из 64 шестнадцатеричных символов")
        if isinstance(context_lines, bool) or not isinstance(context_lines, int) or not 0 <= context_lines <= 20:
            raise PatchError("invalid_arguments", "Число строк контекста должно быть целым от 0 до 20")
        if not isinstance(replacement_content, str):
            raise PatchError("invalid_arguments", "Замена должна быть текстом UTF-8")
        try:
            replacement = replacement_content.encode("utf-8")
        except UnicodeEncodeError:
            raise PatchError("invalid_encoding", "Замена должна быть корректным UTF-8") from None
        if len(replacement) > MAX_SOURCE_BYTES:
            raise PatchError("limit_exceeded", "Замена превышает предел 1 МиБ")
        path, source = self._source(source_path)
        source_sha = hashlib.sha256(source).hexdigest()
        if source_sha != expected_source_sha256.lower():
            raise PatchError("conflict", "Исходник изменился; прочитайте его и SHA-256 повторно")
        before_text, after_text = self._text(source), self._text(replacement)
        # split('\n') сохраняет CRLF и рассматривает только LF как границу diff-строки.
        def lines(text: str) -> list[str]:
            pieces = text.split("\n")
            return [piece + "\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])
        generated = difflib.unified_diff(lines(before_text), lines(after_text),
                                         fromfile="a/" + path, tofile="b/" + path,
                                         n=context_lines, lineterm="\n")
        chunks: list[str] = []
        hunks = 0
        patch_size = 0
        for line in generated:
            if line.startswith("@@ "):
                hunks += 1
            if not line.endswith("\n"):
                line += "\n\\ No newline at end of file\n"
            patch_size += len(line.encode("utf-8"))
            if patch_size > MAX_PATCH_BYTES:
                raise PatchError("limit_exceeded", "Патч превышает предел 2 МиБ")
            chunks.append(line)
        patch_text = "".join(chunks)
        return {"source": "official_modkit", "revision": self.revision, "path": path,
                "source_sha256": source_sha, "replacement_sha256": hashlib.sha256(replacement).hexdigest(),
                "source_bytes": len(source), "replacement_bytes": len(replacement),
                "changed": source != replacement, "hunks": hunks, "context_lines": context_lines,
                "patch_text": patch_text, "patch_bytes": patch_size,
                "validation": "standard_unified_diff", "engine_apply": "not_performed",
                "compiled": False}
