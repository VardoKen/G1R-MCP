"""Ограниченный контур запуска разработки через фиксированные инструменты.

Рецепт и снимок процессов читаются без запуска игры. Start/stop/load выполняют
одну явную операцию; ответ Steam и загрузчика не доказывает компиляцию,
готовность Tick или загрузку выбранного пакета.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable

from .packages import PackageError, validate_package
from .workspace import PackageWorkspace, WorkspaceError, _checked, _guard_directories, _read_bytes

MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_DIRECTORIES = 10_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PROCESS_OUTPUT = 256 * 1024
MAX_MANIFEST_BYTES = 768 * 1024
PROCESS_TIMEOUT_SECONDS = 30
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$")


class DevelopmentError(Exception):
    """Ошибка операции с явным признаком неопределённого внешнего эффекта."""
    def __init__(self, code: str, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


Executor = Callable[[list[str], int], CommandResult]


def execute_command(arguments: list[str], timeout: int) -> CommandResult:
    """Запустить один фиксированный инструмент без shell и без повторов."""
    process = None
    started = False
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        started = True
        chunks = [bytearray(), bytearray()]
        failed = threading.Event()

        def drain(stream, output: bytearray) -> None:
            try:
                while True:
                    fragment = stream.read(4096)
                    if not fragment:
                        break
                    if len(output) + len(fragment) > MAX_PROCESS_OUTPUT:
                        failed.set()
                        process.kill()
                        break
                    output.extend(fragment)
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(stream, output), daemon=True)
                   for stream, output in zip((process.stdout, process.stderr), chunks)]
        for thread in threads:
            thread.start()
        returncode = process.wait(timeout=timeout)
        for thread in threads:
            thread.join(1)
        if failed.is_set() or any(thread.is_alive() for thread in threads):
            raise DevelopmentError("tool_output_too_large", "Ответ инструмента превышает предел или канал не завершился",
                                   outcome_unknown=True)
        return CommandResult(returncode, chunks[0].decode("utf-8"), chunks[1].decode("utf-8"))
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.wait(timeout=2)
        raise DevelopmentError("operation_timeout", "Инструмент не завершился за 30 секунд; исход внешней операции неизвестен",
                               outcome_unknown=True) from None
    except (OSError, UnicodeError):
        raise DevelopmentError("tool_unavailable", "Не удалось запустить или прочитать фиксированный инструмент",
                               outcome_unknown=started) from None
    except DevelopmentError:
        raise
    except BaseException as error:
        # KeyboardInterrupt/SystemExit не оставляют запущенный локальный helper.
        # Внешний эффект (Steam/LoadLibrary) остаётся неопределённым и не повторяется.
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except BaseException:
                pass
        if started:
            error.add_note("Локальный helper остановлен; исход внешней операции неизвестен, повтор запрещён")
        raise


def _unique_json(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _identity(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("pid")) is not int or not 1 <= value["pid"] <= 0xFFFFFFFF:
        raise DevelopmentError("invalid_process_identity", "Нужны PID, creation_time и executable проверенного процесса")
    if not isinstance(value.get("creation_time"), str) or not _TIMESTAMP.fullmatch(value["creation_time"]):
        raise DevelopmentError("invalid_process_identity", "creation_time должен быть точной UTC-меткой из development recipe")
    if not isinstance(value.get("executable"), str) or not value["executable"]:
        raise DevelopmentError("invalid_process_identity", "Отсутствует точный executable процесса")
    return {"pid": value["pid"], "creation_time": value["creation_time"], "executable": value["executable"]}


class DevelopmentService:
    def __init__(self, *, repository_root: str | Path, workspace_root: str | Path,
                 modkit_root: str | Path, game_root: str | Path,
                 executor: Executor = execute_command, powershell: str | None = None):
        try:
            self.repository = _checked(Path(repository_root), directory=True)
            self.game = _checked(Path(game_root), directory=True)
            self.modkit = _checked(Path(modkit_root), directory=True)
            self.workspace = PackageWorkspace(workspace_root, protected_roots=(self.modkit, self.game))
        except WorkspaceError as error:
            raise DevelopmentError(error.code, str(error)) from None
        self.executor = executor
        self.powershell = powershell or str(Path(os.environ.get("WINDIR", r"C:\Windows")) /
                                          "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        self.inner_executable = self.game / "G1R" / "Binaries" / "Win64" / "G1R-Win64-Shipping.exe"
        self.bootstrap_executable = self.game / "G1R-Win64-Shipping.exe"

    def _command(self, script: str, arguments: list[str], *, mutating: bool) -> dict:
        try:
            script_path = _checked(self.repository / "tools" / script, self.repository)
            if not script_path.is_file():
                raise WorkspaceError("invalid_path", "Фиксированный инструмент должен быть обычным файлом")
        except WorkspaceError as error:
            raise DevelopmentError("tool_unavailable", str(error)) from None
        result = self.executor([self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", str(script_path), *arguments, "-Json"], PROCESS_TIMEOUT_SECONDS)
        if len(result.stdout.encode("utf-8")) > MAX_PROCESS_OUTPUT or len(result.stderr.encode("utf-8")) > MAX_PROCESS_OUTPUT:
            raise DevelopmentError("tool_output_too_large", "Ответ инструмента превышает предел",
                                   outcome_unknown=mutating)
        if result.returncode != 0:
            raise DevelopmentError("tool_failed", "Фиксированный инструмент отказал; автоматического повтора нет",
                                   outcome_unknown=mutating)
        try:
            value = json.loads(result.stdout.lstrip("\ufeff"), object_pairs_hook=_unique_json,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError):
            raise DevelopmentError("tool_protocol_error", "Инструмент не вернул корректный структурированный JSON",
                                   outcome_unknown=mutating) from None
        if not isinstance(value, dict):
            raise DevelopmentError("tool_protocol_error", "Корнем ответа инструмента должен быть объект",
                                   outcome_unknown=mutating)
        return value

    def process_state(self) -> dict:
        value = self._command("game_process_state.ps1", ["-GameRoot", str(self.game)], mutating=False)
        processes = value.get("processes")
        if not isinstance(processes, list) or len(processes) > 2:
            raise DevelopmentError("tool_protocol_error", "Неверный список игровых процессов")
        checked = []
        allowed = {os.path.normcase(str(self.inner_executable)), os.path.normcase(str(self.bootstrap_executable))}
        for item in processes:
            identity = _identity(item)
            if os.path.normcase(identity["executable"]) not in allowed:
                raise DevelopmentError("process_path_mismatch", "Игровой процесс имеет неподтверждённый executable")
            checked.append(identity)
        if len({item["pid"] for item in checked}) != len(checked):
            raise DevelopmentError("tool_protocol_error", "Список процессов содержит повторный PID")
        if len({os.path.normcase(item["executable"]) for item in checked}) != len(checked):
            raise DevelopmentError("ambiguous_process", "Обнаружено несколько экземпляров одного игрового EXE")
        return {"processes": checked, "running": bool(checked), "source": "windows_process_snapshot"}

    def _manifest(self, relative_package: str) -> dict:
        try:
            package, normalized = self.workspace._package(relative_package)
            if normalized == ".":
                raise DevelopmentError("invalid_package", "Для запуска нужен отдельный каталог официального пакета")
            validation = validate_package(self.workspace.root, normalized)
            if not validation["valid_structure"]:
                raise DevelopmentError("invalid_package", "Официальный пакет не прошёл структурную проверку")
            name = validation["metadata"]["name"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) or package.name != name:
                raise DevelopmentError("invalid_package_name", "Имя пакета должно точно совпадать с именем каталога")
            entries = []
            total = 0
            file_stamps = {}

            def walk_failure(error: OSError) -> None:
                raise DevelopmentError("access_error", "Не удалось полностью перечислить файлы пакета")

            with _guard_directories(package, self.workspace.root):
                directory_count = 0
                for directory, directories, files in os.walk(package, followlinks=False, onerror=walk_failure):
                    directory_count += 1
                    if directory_count > MAX_PACKAGE_DIRECTORIES:
                        raise DevelopmentError("manifest_limit", "Пакет превышает предел 10000 каталогов")
                    parent = _checked(Path(directory), package, directory=True)
                    for child in directories:
                        _checked(parent / child, package, directory=True)
                    for child in files:
                        file = _checked(parent / child, package)
                        if not file.is_file():
                            raise DevelopmentError("unsafe_package_file", "Пакет содержит не обычный файл")
                        if len(entries) >= MAX_PACKAGE_FILES:
                            raise DevelopmentError("manifest_limit", "Пакет превышает предел 10000 файлов")
                        before = file.stat()
                        data = _read_bytes(file, package, min(MAX_FILE_BYTES, MAX_PACKAGE_BYTES - total))
                        total += len(data)
                        if total > MAX_PACKAGE_BYTES:
                            raise DevelopmentError("manifest_limit", "Пакет превышает общий предел 64 МиБ")
                        entries.append({"path": file.relative_to(package).as_posix(), "bytes": len(data),
                                        "sha256": hashlib.sha256(data).hexdigest()})
                        info = file.stat()
                        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                            raise DevelopmentError("package_changed", "Файл пакета изменился во время чтения manifest")
                        file_stamps[file.relative_to(package).as_posix()] = (info.st_dev, info.st_ino,
                                                                           info.st_size, info.st_mtime_ns)
                after_paths = set()
                directory_count = 0
                for directory, directories, files in os.walk(package, followlinks=False, onerror=walk_failure):
                    directory_count += 1
                    if directory_count > MAX_PACKAGE_DIRECTORIES:
                        raise DevelopmentError("manifest_limit", "Пакет превышает предел 10000 каталогов")
                    parent = _checked(Path(directory), package, directory=True)
                    for child in directories:
                        _checked(parent / child, package, directory=True)
                    for child in files:
                        file = _checked(parent / child, package)
                        relative = file.relative_to(package).as_posix()
                        after_paths.add(relative)
                        info = file.stat()
                        if file_stamps.get(relative) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                            raise DevelopmentError("package_changed", "Пакет изменился во время чтения manifest")
                if after_paths != set(file_stamps):
                    raise DevelopmentError("package_changed", "Состав пакета изменился во время чтения manifest")
            entries.sort(key=lambda item: item["path"])
            # Валидатор должен описывать именно те байты дескриптора, которые вошли в хеш.
            final_validation = validate_package(self.workspace.root, normalized)
            descriptor_entry = next((entry for entry in entries if entry["path"] == "GothicMod.json"), None)
            descriptor_hash = hashlib.sha256(_read_bytes(package / "GothicMod.json", package, MAX_FILE_BYTES)).hexdigest()
            if (not final_validation["valid_structure"] or final_validation["metadata"] != validation["metadata"]
                    or final_validation["script_count"] != validation["script_count"]
                    or final_validation.get("patch_count", 0) != validation.get("patch_count", 0)
                    or descriptor_entry is None or descriptor_entry["sha256"] != descriptor_hash):
                raise DevelopmentError("package_changed", "Дескриптор или структура пакета изменились во время проверки manifest")
            payload = {"package_path": normalized, "package_name": name,
                       "workspace_root": str(self.workspace.root), "modkit_root": str(self.modkit),
                       "game_root": str(self.game), "files": entries}
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if len(serialized) > MAX_MANIFEST_BYTES:
                raise DevelopmentError("manifest_limit", "Полный manifest превышает предел 768 КиБ")
            digest = hashlib.sha256(serialized).hexdigest()
            return {**payload, "manifest_sha256": digest, "total_bytes": total,
                    "script_count": validation["script_count"], "patch_count": validation.get("patch_count", 0),
                    "compiled": False, "runtime_validation": "not_performed"}
        except (PackageError, WorkspaceError) as error:
            raise DevelopmentError(error.code, str(error)) from None
        except OSError:
            raise DevelopmentError("access_error", "Не удалось полностью прочитать пакет для запуска") from None

    def native_artifact(self) -> dict:
        """Получить SHA-256 собранной DLL для явной последующей загрузки."""
        try:
            dll = _checked(self.repository / "build" / "native" / "Release" / "G1R_MCP.dll", self.repository)
            raw = _read_bytes(dll, self.repository, MAX_FILE_BYTES)
        except WorkspaceError as error:
            raise DevelopmentError(error.code, str(error)) from None
        return {"path": str(dll), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "loaded": False, "runtime_validation": "not_performed"}

    def recipe(self, relative_package: str) -> dict:
        manifest = self._manifest(relative_package)
        package = self.workspace.root / manifest["package_path"]
        try:
            artifact = {"available": True, **self.native_artifact()}
        except DevelopmentError as error:
            artifact = {"available": False, "reason_code": error.code}
        return {"manifest": manifest, "process_state": self.process_state(), "native_artifact": artifact,
                "launch": {"mods_directory": str(package.parent), "mods": [manifest["package_name"]],
                           "effects": "Заменить выбор пакетов только в запускаемой игровой сессии",
                           "compilation_trigger": "game_startup", "hot_reload": "function_bodies_file_watch"},
                "diagnostics_available": False, "dedicated_compile_command": False,
                "compiled": False, "runtime_validation": "not_performed"}

    def _expected_process(self, expected_process: dict, state: dict, *, allow_bootstrap: bool = False) -> dict:
        expected = _identity(expected_process)
        allowed = {os.path.normcase(str(self.inner_executable))}
        if allow_bootstrap:
            allowed.add(os.path.normcase(str(self.bootstrap_executable)))
        if os.path.normcase(expected["executable"]) not in allowed:
            raise DevelopmentError("process_path_mismatch", "Ожидаемый процесс имеет неподтверждённый EXE выбранной игры")
        matches = [item for item in state["processes"] if item == expected]
        if len(matches) != 1:
            raise DevelopmentError("process_changed", "PID, время создания или путь игры изменились")
        return expected

    def start(self, relative_package: str, expected_manifest_sha256: str, *, restart: bool = False,
              expected_process: dict | None = None) -> dict:
        if not isinstance(expected_manifest_sha256, str) or not _SHA.fullmatch(expected_manifest_sha256):
            raise DevelopmentError("invalid_manifest_hash", "Нужен SHA-256 полного пакета из development recipe")
        if type(restart) is not bool:
            raise DevelopmentError("invalid_arguments", "restart должен быть bool")
        manifest = self._manifest(relative_package)
        if manifest["manifest_sha256"] != expected_manifest_sha256.lower():
            raise DevelopmentError("package_changed", "Пакет изменился после подготовки рецепта")
        state = self.process_state()
        arguments = ["-GameRoot", str(self.game), "-ModsDirectory",
                     str((self.workspace.root / manifest["package_path"]).parent), "-Mods", manifest["package_name"]]
        if restart:
            expected = self._expected_process(expected_process, state)
            arguments += ["-Restart", "-ExpectedPid", str(expected["pid"]),
                          "-ExpectedCreationTime", expected["creation_time"]]
        elif state["running"]:
            raise DevelopmentError("game_running", "Для работающей игры нужны restart и точная идентичность процесса")
        elif expected_process is not None:
            raise DevelopmentError("invalid_arguments", "expected_process используется только при restart")
        response = self._command("start_g1r_development.ps1", arguments, mutating=True)
        if response.get("state") != "launch_requested" or response.get("package_name") != manifest["package_name"]:
            raise DevelopmentError("tool_protocol_error", "Ответ запуска не подтвердил передачу выбранного пакета Steam",
                                   outcome_unknown=True)
        return {"state": "launch_requested", "manifest_sha256": manifest["manifest_sha256"],
                "package_name": manifest["package_name"], "tool_result": response,
                "compiled": False, "runtime_validation": "not_performed",
                "game_ready": False, "outcome_unknown": False}

    def stop(self, expected_process: dict) -> dict:
        expected = self._expected_process(expected_process, self.process_state(), allow_bootstrap=True)
        response = self._command("stop_g1r_development.ps1", ["-GameRoot", str(self.game),
                                 "-ExpectedPid", str(expected["pid"]),
                                 "-ExpectedCreationTime", expected["creation_time"]], mutating=True)
        if response.get("state") != "stopped" or response.get("process") != expected:
            raise DevelopmentError("tool_protocol_error", "Ответ остановки не подтвердил завершение исходного процесса",
                                   outcome_unknown=True)
        return {"state": "stop_result", "process": expected, "tool_result": response,
                "outcome_unknown": False}

    def load_native(self, expected_process: dict, expected_dll_sha256: str) -> dict:
        if not isinstance(expected_dll_sha256, str) or not _SHA.fullmatch(expected_dll_sha256):
            raise DevelopmentError("invalid_dll_hash", "Нужен SHA-256 проверенной DLL")
        expected = self._expected_process(expected_process, self.process_state())
        try:
            dll = _checked(self.repository / "build" / "native" / "Release" / "G1R_MCP.dll", self.repository)
            data = _read_bytes(dll, self.repository, MAX_FILE_BYTES)
            actual = hashlib.sha256(data).hexdigest()
        except WorkspaceError as error:
            raise DevelopmentError(error.code, str(error)) from None
        if actual != expected_dll_sha256.lower():
            raise DevelopmentError("dll_changed", "DLL изменилась после получения её SHA-256")
        response = self._command("load_native.ps1", ["-GameRoot", str(self.game),
                                 "-ExpectedPid", str(expected["pid"]),
                                 "-ExpectedCreationTime", expected["creation_time"],
                                 "-ExpectedDllSha256", actual], mutating=True)
        if response.get("state") != "loaded" or response.get("process") != expected or response.get("dll_sha256") != actual:
            raise DevelopmentError("tool_protocol_error", "Ответ загрузчика не подтвердил выбранные процесс и DLL",
                                   outcome_unknown=True)
        return {"state": "load_result", "process": expected, "dll_sha256": actual,
                "tool_result": response, "game_ready": False, "outcome_unknown": False}
