"""Создание и редактирование официальных пакетов в выбранной рабочей базе.

Игровые каталоги и официальный Modkit этим модулем не определяются. Доступ
ограничен GothicMod.json, Script/**/*.as и ScriptPatches/*.patch;
компиляция, применение патчей и загрузка не выполняются.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
import threading
from typing import Iterable, Iterator

from .packages import MAX_DESCRIPTOR_BYTES, PackageError, validate_package

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 64 * 1024
MAX_EXACT_READ_BYTES = 1024 * 1024
MAX_FILES = 10_000
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_DIRECTORY_PARTS = 32
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.I)


class WorkspaceError(Exception):
    """Предсказуемая ошибка без раскрытия абсолютных пользовательских путей."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _parts(value: str, *, root_allowed: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise WorkspaceError("invalid_path", "Нужен ограниченный относительный путь")
    if root_allowed and value == ".":
        return ()
    if PureWindowsPath(value).anchor or "\\" in value:
        raise WorkspaceError("unsafe_path", "Нужен относительный путь с прямыми разделителями")
    pieces = tuple(value.split("/"))
    for piece in pieces:
        if (piece in ("", ".", "..") or piece[-1:] in (" ", ".")
                or _RESERVED.fullmatch(piece) or any(ord(c) < 32 or c in '<>:"|?*' for c in piece)):
            raise WorkspaceError("unsafe_path", "Путь содержит недопустимый компонент")
    return pieces


def _checked(path: Path, boundary: Path | None = None, *, directory: bool = False) -> Path:
    try:
        absolute = Path(os.path.abspath(path))
        for part in (absolute, *absolute.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise WorkspaceError("unsafe_path", "Путь содержит ссылку или reparse point")
        resolved = absolute.resolve(strict=True)
        if boundary is not None and not resolved.is_relative_to(boundary):
            raise WorkspaceError("unsafe_path", "Путь выходит за пределы рабочей базы")
        info = resolved.stat()
        if directory and not stat.S_ISDIR(info.st_mode):
            raise WorkspaceError("invalid_path", "Путь должен быть каталогом")
        if not directory and not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise WorkspaceError("invalid_path", "Путь имеет неподходящий тип")
        return resolved
    except WorkspaceError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise WorkspaceError("access_error", "Путь отсутствует или недоступен") from None


@contextmanager
def _guard_directories(directory: Path, boundary: Path) -> Iterator[None]:
    """На Windows удерживать каталоги без FILE_SHARE_DELETE при операции."""
    handles: list[int] = []
    try:
        _checked(directory, boundary, directory=True)
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                               ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            create.restype = ctypes.c_void_p
            close = kernel.CloseHandle
            close.argtypes = [ctypes.c_void_p]
            close.restype = ctypes.c_int
            for part in reversed((directory, *directory.parents)):
                handle = create(str(part), 0, 3, None, 3, 0x02200000, None)
                if handle in (None, ctypes.c_void_p(-1).value):
                    raise WorkspaceError("access_error", "Не удалось защитить рабочий каталог")
                handles.append(handle)
            _checked(directory, boundary, directory=True)
        yield
    finally:
        if handles:
            for handle in reversed(handles):
                close(handle)


@contextmanager
def _destination_lock(path: Path) -> Iterator[None]:
    """Сериализовать запись одного назначения между MCP-процессами без файлов."""
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    wait = kernel.WaitForSingleObject
    wait.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait.restype = ctypes.c_uint32
    release = kernel.ReleaseMutex
    release.argtypes = [ctypes.c_void_p]
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    name = "Local\\G1R-MCP-workspace-" + hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
    handle = create(None, False, name)
    if not handle:
        raise WorkspaceError("access_error", "Не удалось защитить запись назначения")
    acquired = False
    try:
        outcome = wait(handle, 5000)
        acquired = outcome in (0, 0x80)
        if not acquired:
            raise WorkspaceError("conflict", "Назначение занято другой записью; повторите запрос")
        yield
    finally:
        if acquired:
            release(handle)
        close(handle)


def _read_bytes(path: Path, boundary: Path, maximum: int) -> bytes:
    checked = _checked(path, boundary)
    if not checked.is_file():
        raise WorkspaceError("invalid_path", "Исходник должен быть обычным файлом")
    try:
        before = checked.stat()
        if before.st_size > maximum:
            raise WorkspaceError("limit_exceeded", "Файл превышает разрешённый размер")
        with checked.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
                raise WorkspaceError("file_changed", "Файл изменился во время чтения")
            raw = stream.read(maximum + 1)
            after_read = os.fstat(stream.fileno())
        _checked(checked, boundary)
        after = checked.stat()
        stamp = lambda info: (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
        if stamp(before) != stamp(after_read) or stamp(before) != stamp(after):
            raise WorkspaceError("file_changed", "Файл изменился во время чтения")
        if len(raw) > maximum:
            raise WorkspaceError("limit_exceeded", "Файл превышает разрешённый размер")
        return raw
    except WorkspaceError:
        raise
    except OSError:
        raise WorkspaceError("access_error", "Не удалось прочитать файл") from None


def _result(**values) -> dict:
    return {"source": "workspace_package", "compiled": False,
            "runtime_validation": "not_performed", **values}


class PackageWorkspace:
    def __init__(self, workspace_root: str | Path, *, protected_roots: Iterable[str | Path] = ()):
        self.root = _checked(Path(workspace_root), directory=True)
        self.protected_roots = tuple(_checked(Path(root), directory=True) for root in protected_roots)
        self._lock = threading.RLock()

    def _unprotected(self, path: Path, *, forbid_ancestor: bool = False) -> Path:
        """Проверить каноническое назначение; общий родитель рабочих баз допустим."""
        for protected in self.protected_roots:
            if path.is_relative_to(protected) or (forbid_ancestor and protected.is_relative_to(path)):
                raise WorkspaceError("unsafe_path", "Путь относится к защищённому каталогу исходников")
        return path

    def _package(self, relative_path: str) -> tuple[Path, str]:
        parts = _parts(relative_path, root_allowed=True)
        package = self._unprotected(_checked(self.root.joinpath(*parts), self.root, directory=True))
        return package, "/".join(parts) or "."

    @staticmethod
    def _file_parts(file_path: str) -> tuple[str, ...]:
        parts = _parts(file_path)
        if parts == ("GothicMod.json",):
            return parts
        if len(parts) >= 2 and parts[0] == "Script" and Path(parts[-1]).suffix.lower() == ".as":
            return parts
        if len(parts) == 2 and parts[0] == "ScriptPatches" and Path(parts[-1]).suffix.lower() == ".patch":
            return parts
        raise WorkspaceError("unsupported_file", "Разрешены GothicMod.json, Script/**/*.as и ScriptPatches/*.patch")

    @contextmanager
    def _write_parent(self, package: Path, parts: tuple[str, ...]) -> Iterator[Path]:
        parent = package.joinpath(*parts[:-1])
        self._unprotected(parent)
        if parts[0] != "ScriptPatches" or os.path.lexists(parent):
            yield _checked(parent, package, directory=True)
            return
        # Создание единственного документированного каталога сериализовано отдельно.
        with _destination_lock(parent), _guard_directories(package, self.root):
            created = False
            try:
                if not os.path.lexists(parent):
                    parent.mkdir()
                    created = True
                yield self._unprotected(_checked(parent, package, directory=True))
            except BaseException as error:
                if created:
                    # Удаляется лишь наша пустая папка; чужие появившиеся файлы сохраняются.
                    checked = _checked(parent, package, directory=True)
                    if not any(checked.iterdir()):
                        checked.rmdir()
                if isinstance(error, OSError):
                    raise WorkspaceError("access_error", "Не удалось подготовить каталог патчей") from None
                raise

    def create_package(self, relative_path: str, metadata: dict) -> dict:
        parts = _parts(relative_path)
        if not isinstance(metadata, dict):
            raise WorkspaceError("invalid_arguments", "Метаданные должны быть JSON-объектом")
        try:
            raw = (json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        except (ValueError, TypeError, UnicodeEncodeError, RecursionError):
            raise WorkspaceError("invalid_descriptor", "Метаданные не являются корректным UTF-8 JSON") from None
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise WorkspaceError("limit_exceeded", "Дескриптор превышает предел 256 КиБ")
        with self._lock:
            parent = _checked(self.root.joinpath(*parts[:-1]), self.root, directory=True)
            target = self._unprotected(parent / parts[-1], forbid_ancestor=True)
            with _destination_lock(target), _guard_directories(parent, self.root):
                if os.path.lexists(target):
                    raise WorkspaceError("conflict", "Каталог пакета уже существует")
                stage: Path | None = None
                try:
                    stage = Path(tempfile.mkdtemp(prefix=".g1r-mcp-", dir=parent))
                    (stage / "Script").mkdir()
                    (stage / "GothicMod.json").write_bytes(raw)
                    report = validate_package(parent, stage.name)
                    if not report["valid_structure"]:
                        raise WorkspaceError("invalid_descriptor", "Дескриптор не прошёл структурную проверку")
                    if os.path.lexists(target):
                        raise WorkspaceError("conflict", "Каталог пакета появился во время создания")
                    # На Windows rename атомарно отказывает при существующем назначении.
                    if os.name != "nt":
                        raise WorkspaceError("unsupported_environment", "Атомарное создание пакета поддержано на Windows")
                    stage.rename(target)
                    stage = None
                except WorkspaceError:
                    raise
                except OSError:
                    raise WorkspaceError("access_error", "Не удалось создать пакет") from None
                finally:
                    if stage is not None:
                        (stage / "GothicMod.json").unlink(missing_ok=True)
                        if (stage / "Script").exists():
                            (stage / "Script").rmdir()
                        stage.rmdir()
            report["package_path"] = "/".join(parts)
            return _result(package_path="/".join(parts), created=True,
                           files=["GothicMod.json"], directories=["Script"],
                           sha256=hashlib.sha256(raw).hexdigest(), validation=report)

    def create_package_directory(self, relative_path: str, directory_path: str) -> dict:
        """Создать только каталоги исходников существующего официального пакета."""
        parts = _parts(directory_path)
        if len(parts) > MAX_DIRECTORY_PARTS:
            raise WorkspaceError("limit_exceeded", "Путь каталога превышает предел 32 компонентов")
        if parts[0] != "Script" and parts != ("ScriptPatches",):
            raise WorkspaceError("unsupported_directory", "Разрешены Script[/подкаталоги] и ScriptPatches")
        with self._lock:
            package, name = self._package(relative_path)
            created: list[tuple[Path, tuple[int, int]]] = []
            with _destination_lock(package), _guard_directories(package, self.root):
                descriptor = self._unprotected(_checked(package / "GothicMod.json", package))
                if not descriptor.is_file():
                    raise WorkspaceError("invalid_descriptor", "Пакет должен содержать обычный GothicMod.json")
                # Проверить все назначения до первого mkdir, включая будущие отсутствующие.
                current = package
                targets: list[Path] = []
                for component in parts:
                    current = self._unprotected(current / component)
                    if os.path.lexists(current):
                        checked = self._unprotected(_checked(current, package))
                        if not checked.is_dir():
                            raise WorkspaceError("conflict", "Компонент пути уже занят файлом")
                    targets.append(current)
                try:
                    report = validate_package(self.root, relative_path)
                except PackageError as error:
                    raise WorkspaceError(error.code, str(error)) from None
                if not report["valid_structure"]:
                    raise WorkspaceError("invalid_descriptor", "Структура существующего пакета не прошла проверку")
                try:
                    with ExitStack() as guards:
                        for target in targets:
                            guards.enter_context(_destination_lock(target))
                            _checked(target.parent, package, directory=True)
                            self._unprotected(target)
                            if os.path.lexists(target):
                                checked = self._unprotected(_checked(target, package))
                                if not checked.is_dir():
                                    raise WorkspaceError("conflict", "Компонент пути уже занят файлом")
                            else:
                                try:
                                    target.mkdir()
                                except FileExistsError:
                                    checked = self._unprotected(_checked(target, package))
                                    if not checked.is_dir():
                                        raise WorkspaceError("conflict", "Компонент пути появился как файл") from None
                                else:
                                    info = target.lstat()
                                    created.append((target, (info.st_dev, info.st_ino)))
                            guards.enter_context(_guard_directories(target, self.root))
                except BaseException as error:
                    # Закрыть no-delete handles перед rmdir; затем проверить путь и владельца.
                    for target, identity in reversed(created):
                        try:
                            with _guard_directories(target.parent, self.root):
                                checked = self._unprotected(_checked(target, package, directory=True))
                                info = checked.stat()
                                if (info.st_dev, info.st_ino) == identity and not any(checked.iterdir()):
                                    checked.rmdir()
                        except (WorkspaceError, OSError):
                            # Изменённый каталог или постороннее содержимое сохраняются.
                            continue
                    if isinstance(error, OSError):
                        raise WorkspaceError("access_error", "Не удалось создать каталоги исходников") from None
                    raise
            paths = [path.relative_to(package).as_posix() for path, _ in created]
            return _result(package_path=name, directory_path="/".join(parts), created=bool(paths),
                           created_paths=paths, created_count=len(paths), exists=True)

    def list_package_files(self, relative_path: str, limit: int = 100) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise WorkspaceError("invalid_arguments", "Предел списка должен быть целым числом от 1 до 200")
        with self._lock:
            package, name = self._package(relative_path)
            files: list[dict] = []
            descriptor = package / "GothicMod.json"
            if os.path.lexists(descriptor):
                file = self._unprotected(_checked(descriptor, package))
                if not file.is_file():
                    raise WorkspaceError("invalid_path", "Дескриптор должен быть обычным файлом")
                files.append({"path": "GothicMod.json", "bytes": file.stat().st_size})
            script = package / "Script"
            if os.path.lexists(script):
                self._unprotected(_checked(script, package, directory=True))
                def walk_error(error: OSError) -> None:
                    raise WorkspaceError("access_error", "Не удалось прочитать каталог исходников")
                for directory, dirs, names in os.walk(script, followlinks=False, onerror=walk_error):
                    parent = self._unprotected(_checked(Path(directory), package, directory=True))
                    for child in dirs:
                        self._unprotected(_checked(parent / child, package, directory=True))
                    for child in names:
                        if Path(child).suffix.lower() != ".as":
                            continue
                        file = self._unprotected(_checked(parent / child, package))
                        if not file.is_file():
                            raise WorkspaceError("invalid_path", "Исходник должен быть обычным файлом")
                        files.append({"path": file.relative_to(package).as_posix(), "bytes": file.stat().st_size})
                        if len(files) > MAX_FILES:
                            raise WorkspaceError("limit_exceeded", "Превышен предел количества файлов")
            patches = package / "ScriptPatches"
            if os.path.lexists(patches):
                checked = self._unprotected(_checked(patches, package, directory=True))
                try:
                    for child in checked.iterdir():
                        self._unprotected(_checked(child, package))
                        if child.suffix.lower() != ".patch":
                            continue
                        if not child.is_file():
                            raise WorkspaceError("invalid_path", "Патч должен быть обычным файлом")
                        files.append({"path": child.relative_to(package).as_posix(), "bytes": child.stat().st_size})
                        if len(files) > MAX_FILES:
                            raise WorkspaceError("limit_exceeded", "Превышен предел количества файлов")
                except OSError:
                    raise WorkspaceError("access_error", "Не удалось прочитать каталог патчей") from None
            files.sort(key=lambda item: item["path"])
            return _result(package_path=name, items=files[:limit], total=len(files),
                           truncated=len(files) > limit, snapshot_atomic=False)

    def read_package_file(self, relative_path: str, file_path: str,
                          start_line: int = 1, line_count: int = 100) -> dict:
        parts = self._file_parts(file_path)
        if (isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1
                or isinstance(line_count, bool) or not isinstance(line_count, int) or not 1 <= line_count <= 400):
            raise WorkspaceError("invalid_arguments", "Нужны положительный номер строки и число строк от 1 до 400")
        with self._lock:
            package, name = self._package(relative_path)
            path = self._unprotected(_checked(package.joinpath(*parts), package))
            raw = _read_bytes(path, package,
                              MAX_DESCRIPTOR_BYTES if len(parts) == 1 else MAX_FILE_BYTES)
            try:
                lines = raw.decode("utf-8-sig").splitlines()
            except UnicodeDecodeError:
                raise WorkspaceError("invalid_encoding", "Файл должен быть корректным UTF-8") from None
            selected = lines[start_line - 1:start_line - 1 + line_count]
            text = "\n".join(selected)
            limited = text.encode("utf-8")[:MAX_READ_BYTES].decode("utf-8", errors="ignore")
            truncated = limited != text
            return _result(package_path=name, file_path="/".join(parts), sha256=hashlib.sha256(raw).hexdigest(),
                           bytes=len(raw), total_lines=len(lines), start_line=start_line,
                           returned_lines=len(limited.splitlines()), text=limited, truncated=truncated,
                           text_mode="display_lines",
                           has_more=truncated or start_line - 1 + len(selected) < len(lines))

    def read_package_file_exact(self, relative_path: str, file_path: str) -> dict:
        """Вернуть полный редактируемый UTF-8 текст без изменения BOM, строк и EOF."""
        parts = self._file_parts(file_path)
        with self._lock:
            package, name = self._package(relative_path)
            path = self._unprotected(_checked(package.joinpath(*parts), package))
            maximum = MAX_DESCRIPTOR_BYTES if len(parts) == 1 else MAX_EXACT_READ_BYTES
            with _guard_directories(path.parent, self.root):
                if path.stat().st_size > maximum:
                    raise WorkspaceError("file_too_large", "Файл превышает предел полного текстового чтения")
                raw = _read_bytes(path, package, maximum)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise WorkspaceError("invalid_encoding", "Файл должен быть корректным UTF-8") from None
            return _result(package_path=name, file_path="/".join(parts), sha256=hashlib.sha256(raw).hexdigest(),
                           bytes=len(raw), text=text, text_mode="exact", truncated=False,
                           max_read_bytes=maximum)

    def write_package_file(self, relative_path: str, file_path: str, content: str,
                           expected_sha256: str | None = None) -> dict:
        parts = self._file_parts(file_path)
        if not isinstance(content, str) or "\0" in content:
            raise WorkspaceError("invalid_arguments", "Содержимое должно быть текстом без NUL")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)):
            raise WorkspaceError("invalid_arguments", "Ожидаемый SHA-256 должен содержать 64 шестнадцатеричных символа")
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspaceError("invalid_encoding", "Содержимое должно быть корректным UTF-8") from None
        maximum = MAX_DESCRIPTOR_BYTES if len(parts) == 1 else MAX_CONTENT_BYTES
        if len(raw) > maximum:
            raise WorkspaceError("limit_exceeded", "Содержимое превышает разрешённый размер")
        with self._lock:
            package, name = self._package(relative_path)
            parent = package.joinpath(*parts[:-1])
            target = self._unprotected(parent / parts[-1])
            if parts[0] == "ScriptPatches" and not os.path.lexists(parent) and expected_sha256 is not None:
                raise WorkspaceError("conflict", "Файл для замены отсутствует")
            with _destination_lock(package), self._write_parent(package, parts), _destination_lock(target), _guard_directories(parent, self.root):
                def verify_destination() -> None:
                    exists = os.path.lexists(target)
                    if expected_sha256 is None:
                        if exists:
                            raise WorkspaceError("conflict", "Файл уже существует; для замены нужен его SHA-256")
                    elif not exists:
                        raise WorkspaceError("conflict", "Файл для замены отсутствует")
                    else:
                        old = _read_bytes(target, package, maximum)
                        if hashlib.sha256(old).hexdigest() != expected_sha256.lower():
                            raise WorkspaceError("conflict", "Файл изменился; прочитайте его повторно")
                verify_destination()
                temporary: Path | None = None
                validation = None
                try:
                    fd, path = tempfile.mkstemp(prefix=".g1r-mcp-", dir=parent)
                    temporary = Path(path)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if len(parts) == 1:
                        # Проверка кандидата в изолированной временной структуре, без замены оригинала.
                        with tempfile.TemporaryDirectory(prefix="g1r-mcp-validation-") as stage_name:
                            stage = Path(stage_name)
                            (stage / "GothicMod.json").write_bytes(raw)
                            validation = validate_package(stage, ".")
                        if not validation["valid_structure"]:
                            raise WorkspaceError("invalid_descriptor", "Дескриптор не прошёл структурную проверку")
                    verify_destination()
                    _checked(parent, package, directory=True)
                    if expected_sha256 is None:
                        try:
                            os.link(temporary, target, follow_symlinks=False)
                        except FileExistsError:
                            raise WorkspaceError("conflict", "Файл появился во время создания") from None
                        temporary.unlink()
                    else:
                        os.replace(temporary, target)
                    temporary = None
                except WorkspaceError:
                    raise
                except (PackageError, OSError):
                    raise WorkspaceError("access_error", "Не удалось записать файл") from None
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return _result(package_path=name, file_path="/".join(parts), written=True,
                           created=expected_sha256 is None, bytes=len(raw),
                           sha256=hashlib.sha256(raw).hexdigest(), validation=validation)
