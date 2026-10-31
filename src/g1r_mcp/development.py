"""Ограниченный контур запуска разработки через фиксированные инструменты.

Рецепт и снимок процессов читаются без запуска игры. Start/stop/load выполняют
одну явную операцию; ответ Steam и загрузчика не доказывает компиляцию,
готовность Tick или загрузку выбранного пакета.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid
from typing import Callable

from .packages import PackageError, validate_package
from .workspace import (PackageWorkspace, WorkspaceError, _checked, _destination_lock,
                        _guard_directories, _read_bytes)

MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_DIRECTORIES = 10_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_PROCESS_OUTPUT = 256 * 1024
MAX_MANIFEST_BYTES = 768 * 1024
MAX_DEVELOPMENT_PACKAGES = 4
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
                 executor: Executor = execute_command, powershell: str | None = None,
                 process_lease_factory: Callable[[dict], object] | None = None):
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
        self._observation_lock = threading.RLock()
        self._launch_observation: dict | None = None
        self._process_lease = None
        self._process_lease_factory = process_lease_factory
        self._closed = False

    def close(self) -> None:
        """Освободить только удерживаемый read-only HANDLE; не завершать игру."""
        with self._observation_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._close_process_lease()
            finally:
                if self._launch_observation is not None:
                    self._launch_observation["observer_closed"] = True
                    if self._launch_observation["stage"] != "process_exited":
                        self._launch_observation.update(stage="observation_closed", outcome_unknown=True)

    def _close_process_lease(self) -> None:
        lease, self._process_lease = self._process_lease, None
        if lease is not None:
            lease.close()

    def _observe_launch(self, processes: list[dict]) -> dict | None:
        record = self._launch_observation
        if record is None:
            return None
        if self._closed or record.get("stage") == "process_exited":
            return copy.deepcopy(record)
        if self._process_lease is None and record["process"] is None and not record.get("capture_attempted"):
            inner = [item for item in processes if os.path.normcase(item["executable"]) == os.path.normcase(str(self.inner_executable))]
            if not inner:
                record.update(stage="launch_unobserved", outcome_unknown=True)
                return copy.deepcopy(record)
            identity = inner[0]
            # Не присваивать запоздалой заявке ранее существовавший процесс.
            created = datetime.fromisoformat(identity["creation_time"].replace("Z", "+00:00"))
            requested = datetime.fromisoformat(record["launch_request_started_at"])
            if created < requested:
                record.update(stage="capture_unavailable", capture_attempted=True,
                              observation_error_code="process_predates_launch", outcome_unknown=True)
                return copy.deepcopy(record)
            record.update(capture_attempted=True, process=dict(identity), process_observed=True,
                          process_correlation="first_inner_snapshot_after_launch")
            try:
                factory = self._process_lease_factory
                if factory is None:
                    from .process_observer import capture_checked_process
                    factory = capture_checked_process
                self._process_lease = factory(identity)
            except Exception as error:
                record.update(stage="capture_unavailable", outcome_unknown=True,
                              observation_error_code=getattr(error, "code", "process_observer_unavailable"))
                return copy.deepcopy(record)
        if self._process_lease is not None:
            try:
                observed = self._process_lease.poll()
                if not isinstance(observed, dict) or observed.get("process") != record["process"]:
                    raise ValueError("observer identity mismatch")
                state = observed.get("state")
                if state == "running":
                    record.update(stage="process_running", outcome_unknown=False)
                elif state == "exited":
                    exit_code = observed.get("exit_code")
                    exit_time = observed.get("exit_time")
                    if type(exit_code) is not int or not 0 <= exit_code <= 0xFFFFFFFF or not isinstance(exit_time, str):
                        raise ValueError("invalid observer exit result")
                    record.update(stage="process_exited", exit_code=exit_code, exit_time=exit_time,
                                  outcome_unknown=False, termination_cause="unknown",
                                  process_failed=exit_code != 0)
                    try:
                        self._close_process_lease()
                    except Exception as error:
                        # Не терять уже прочитанный exitcode из-за ошибки освобождения HANDLE.
                        record["observer_close_error"] = getattr(error, "code", "process_handle_close_failed")
                else:
                    record.update(stage="observation_unknown", outcome_unknown=True,
                                  observation_error_code=observed.get("error_code", "process_observation_failed"))
            except Exception as error:
                record.update(stage="observation_unknown", outcome_unknown=True,
                              observation_error_code=getattr(error, "code", "process_observation_failed"))
        return copy.deepcopy(record)

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
        with self._observation_lock:
            return self._process_state()

    def _process_state(self) -> dict:
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
        return {"processes": checked, "running": bool(checked), "source": "windows_process_snapshot",
                "launch_observation": self._observe_launch(checked)}

    def _manifest(self, relative_package: str, *, enable_ingame_tools: bool = False) -> dict:
        if type(enable_ingame_tools) is not bool:
            raise DevelopmentError("invalid_arguments", "enable_ingame_tools должен быть bool")
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
                       "game_root": str(self.game), "files": entries,
                       "launch_options": {"enable_ingame_tools": enable_ingame_tools}}
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

    def _composition_manifest(self, relative_package: str, companion_packages: list[str] | None,
                              *, enable_ingame_tools: bool = False) -> dict:
        if companion_packages is not None and (not isinstance(companion_packages, list)
                or any(not isinstance(item, str) or not item for item in companion_packages)):
            raise DevelopmentError("invalid_arguments", "companion_packages должен быть списком путей пакетов")
        if not companion_packages:
            return self._manifest(relative_package, enable_ingame_tools=enable_ingame_tools)
        if len(companion_packages) >= MAX_DEVELOPMENT_PACKAGES:
            raise DevelopmentError("manifest_limit", "Development-сеанс поддерживает не более четырёх пакетов")
        packages = [self._manifest(path, enable_ingame_tools=enable_ingame_tools)
                    for path in [relative_package, *companion_packages]]
        names = [item["package_name"] for item in packages]
        if len({name.casefold() for name in names}) != len(names):
            raise DevelopmentError("duplicate_package", "Имена выбранных пакетов должны быть уникальны без учёта регистра")
        source_paths = [os.path.normcase(str(self.workspace.root / item["package_path"])) for item in packages]
        if len(set(source_paths)) != len(source_paths):
            raise DevelopmentError("duplicate_package", "Один пакет нельзя выбрать несколько раз")
        payload = {"composition": "explicit_companion_packages", "package_path": packages[0]["package_path"],
                   "package_name": names[0], "workspace_root": str(self.workspace.root),
                   "modkit_root": str(self.modkit), "game_root": str(self.game),
                   "packages": [{"role": "main" if index == 0 else "companion", **item}
                                for index, item in enumerate(packages)],
                   "selected_mods": names, "launch_options": {"enable_ingame_tools": enable_ingame_tools}}
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(serialized) > MAX_MANIFEST_BYTES:
            raise DevelopmentError("manifest_limit", "Составной manifest превышает предел 768 КиБ")
        return {**payload, "manifest_sha256": hashlib.sha256(serialized).hexdigest(),
                "total_bytes": sum(item["total_bytes"] for item in packages),
                "compiled": False, "runtime_validation": "not_performed"}

    def _stage_composition(self, manifest: dict) -> dict:
        """Скопировать подтверждённые байты в новый сохраняемый рабочий development-сеанс."""
        sessions = self.workspace.root / "artifacts" / "development-sessions"
        stage = sessions / uuid.uuid4().hex
        package_root = stage / "packages"
        try:
            with _destination_lock(self.workspace.root), _guard_directories(self.workspace.root, self.workspace.root):
                for parent in (sessions.parent, sessions):
                    with _guard_directories(parent.parent, self.workspace.root):
                        if not os.path.lexists(parent):
                            parent.mkdir()
                        self.workspace._unprotected(_checked(parent, self.workspace.root, directory=True))
                with _guard_directories(sessions, self.workspace.root):
                    if os.path.lexists(stage):
                        raise DevelopmentError("staging_conflict", "Development-сеанс уже существует")
                    stage.mkdir()
                    self.workspace._unprotected(_checked(stage, self.workspace.root, directory=True))
                    with _guard_directories(stage, self.workspace.root):
                        package_root.mkdir()
                        self.workspace._unprotected(_checked(package_root, self.workspace.root, directory=True))
            copied = []
            for source in manifest["packages"]:
                package_name = source["package_name"]
                target = package_root / package_name
                with _destination_lock(package_root), _guard_directories(package_root, self.workspace.root):
                    target.mkdir()
                    self.workspace._unprotected(_checked(target, self.workspace.root, directory=True))
                    with _guard_directories(target, self.workspace.root):
                        (target / "Script").mkdir()
                source_root, _ = self.workspace._package(source["package_path"])
                for entry in source["files"]:
                    data = _read_bytes(source_root / entry["path"], source_root, MAX_FILE_BYTES)
                    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise DevelopmentError("package_changed", "Исходный пакет изменился при подготовке development-сеанса")
                    destination = target / entry["path"]
                    with _destination_lock(target), _guard_directories(target, self.workspace.root):
                        current = target
                        for component in Path(entry["path"]).parts[:-1]:
                            with _guard_directories(current, self.workspace.root):
                                current = current / component
                                if not os.path.lexists(current):
                                    current.mkdir()
                                self.workspace._unprotected(_checked(current, self.workspace.root, directory=True))
                        with _guard_directories(destination.parent, self.workspace.root):
                            with destination.open("xb") as stream:
                                stream.write(data)
                relative = target.relative_to(self.workspace.root).as_posix()
                staged = self._manifest(relative, enable_ingame_tools=manifest["launch_options"]["enable_ingame_tools"])
                for key in ("package_name", "files", "total_bytes", "script_count", "patch_count"):
                    if staged[key] != source[key]:
                        raise DevelopmentError("staging_changed", "Рабочая копия не совпадает с подтверждёнными байтами пакета")
                copied.append({"role": source["role"], "source_package_path": source["package_path"],
                               "source_manifest_sha256": source["manifest_sha256"],
                               "relative_package": relative, "package_name": package_name,
                               "manifest_sha256_at_launch": staged["manifest_sha256"], "manifest": staged})
            stage_info = {"relative_directory": stage.relative_to(self.workspace.root).as_posix(),
                          "mods_directory": str(package_root), "packages": copied,
                          "source_manifest_sha256": manifest["manifest_sha256"],
                          "edit_target": "staged_copy_only", "retention": "retain_for_review",
                          "hot_reload": "function_bodies_file_watch"}
            with _guard_directories(stage, self.workspace.root):
                with (stage / "session.json").open("x", encoding="utf-8") as stream:
                    json.dump(stage_info, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self._verify_staged_composition(stage_info)
            return stage_info
        except (OSError, WorkspaceError) as error:
            raise DevelopmentError("staging_error", "Не удалось подготовить отдельный рабочий development-сеанс") from error

    def _verify_staged_composition(self, stage: dict) -> None:
        root = self.workspace._unprotected(_checked(Path(stage["mods_directory"]), self.workspace.root, directory=True))
        expected = {item["package_name"] for item in stage["packages"]}
        with _guard_directories(root, self.workspace.root):
            if {item.name for item in root.iterdir()} != expected:
                raise DevelopmentError("staging_changed", "Состав рабочего development-сеанса изменился")
            for item in stage["packages"]:
                current = self._manifest(item["relative_package"],
                    enable_ingame_tools=item["manifest"]["launch_options"]["enable_ingame_tools"])
                if current["manifest_sha256"] != item["manifest_sha256_at_launch"]:
                    raise DevelopmentError("staging_changed", "Рабочий пакет изменился до запуска игры")

    def native_artifact(self) -> dict:
        """Получить SHA-256 собранной DLL для явной последующей загрузки."""
        try:
            dll = _checked(self.repository / "build" / "native" / "Release" / "G1R_MCP.dll", self.repository)
            raw = _read_bytes(dll, self.repository, MAX_FILE_BYTES)
        except WorkspaceError as error:
            raise DevelopmentError(error.code, str(error)) from None
        return {"path": str(dll), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "loaded": False, "runtime_validation": "not_performed"}

    def recipe(self, relative_package: str, *, companion_packages: list[str] | None = None,
               enable_ingame_tools: bool = False) -> dict:
        manifest = self._composition_manifest(relative_package, companion_packages,
                                              enable_ingame_tools=enable_ingame_tools)
        package = self.workspace.root / manifest["package_path"]
        try:
            artifact = {"available": True, **self.native_artifact()}
        except DevelopmentError as error:
            artifact = {"available": False, "reason_code": error.code}
        composed = "packages" in manifest
        return {"manifest": manifest, "process_state": self.process_state(), "native_artifact": artifact,
                "launch": {"mods_directory": None if composed else str(package.parent),
                           "mods": manifest["selected_mods"] if composed else [manifest["package_name"]],
                           "enable_ingame_tools": enable_ingame_tools,
                           "effects": "Заменить выбор пакетов только в запускаемой игровой сессии",
                           "compilation_trigger": "game_startup", "hot_reload": "function_bodies_file_watch",
                           **({"staging_created_by": "development_start", "edit_target": "staged_copy_only",
                               "staging_retention": "retain_for_review", "source_packages_modified": False}
                              if composed else {})},
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
              expected_process: dict | None = None, companion_packages: list[str] | None = None,
              enable_ingame_tools: bool = False) -> dict:
        with self._observation_lock:
            if self._closed:
                raise DevelopmentError("service_closed", "Контур наблюдения уже закрыт")
            return self._start(relative_package, expected_manifest_sha256, restart=restart,
                               expected_process=expected_process, companion_packages=companion_packages,
                               enable_ingame_tools=enable_ingame_tools)

    def _start(self, relative_package: str, expected_manifest_sha256: str, *, restart: bool = False,
               expected_process: dict | None = None, companion_packages: list[str] | None = None,
               enable_ingame_tools: bool = False) -> dict:
        if not isinstance(expected_manifest_sha256, str) or not _SHA.fullmatch(expected_manifest_sha256):
            raise DevelopmentError("invalid_manifest_hash", "Нужен SHA-256 полного пакета из development recipe")
        if type(restart) is not bool:
            raise DevelopmentError("invalid_arguments", "restart должен быть bool")
        manifest = self._composition_manifest(relative_package, companion_packages,
                                              enable_ingame_tools=enable_ingame_tools)
        if manifest["manifest_sha256"] != expected_manifest_sha256.lower():
            raise DevelopmentError("package_changed", "Пакет изменился после подготовки рецепта")
        state = self.process_state()
        composed = "packages" in manifest
        arguments = ["-GameRoot", str(self.game)]
        if enable_ingame_tools:
            arguments.append("-EnableIngameTools")
        if restart:
            expected = self._expected_process(expected_process, state)
            arguments += ["-Restart", "-ExpectedPid", str(expected["pid"]),
                          "-ExpectedCreationTime", expected["creation_time"]]
        elif state["running"]:
            raise DevelopmentError("game_running", "Для работающей игры нужны restart и точная идентичность процесса")
        elif expected_process is not None:
            raise DevelopmentError("invalid_arguments", "expected_process используется только при restart")
        stage = self._stage_composition(manifest) if composed else None
        if stage is not None:
            fresh = self._composition_manifest(relative_package, companion_packages,
                                               enable_ingame_tools=enable_ingame_tools)
            if fresh["manifest_sha256"] != manifest["manifest_sha256"]:
                raise DevelopmentError("package_changed", "Исходные пакеты изменились при подготовке development-сеанса")
            self._verify_staged_composition(stage)
            arguments.extend(["-ModsDirectory", stage["mods_directory"], "-ModsCsv",
                              ",".join(manifest["selected_mods"])])
        else:
            arguments.extend(["-ModsDirectory", str((self.workspace.root / manifest["package_path"]).parent),
                              "-Mods", manifest["package_name"]])
        try:
            self._close_process_lease()
        except Exception:
            raise DevelopmentError("observer_close_failed", "Старый HANDLE не освобождён; новый запуск не выполнялся") from None
        if self._launch_observation is not None and self._launch_observation["stage"] != "process_exited":
            self._launch_observation.update(stage="observation_closed", observer_closed=True, outcome_unknown=True)
        request_started_at = datetime.now(timezone.utc).isoformat()
        response = self._command("start_g1r_development.ps1", arguments, mutating=True)
        receipt_matches = response.get("package_name") == manifest["package_name"]
        if stage is not None:
            receipt_root = response.get("mods_directory")
            receipt_matches = (response.get("mods") == manifest["selected_mods"]
                and isinstance(receipt_root, str)
                and os.path.normcase(receipt_root) == os.path.normcase(stage["mods_directory"]))
        if response.get("state") != "launch_requested" or not receipt_matches:
            raise DevelopmentError("tool_protocol_error", "Ответ запуска не подтвердил передачу выбранного пакета Steam",
                                   outcome_unknown=True)
        self._launch_observation = {"launch_id": str(uuid.uuid4()),
            "manifest_sha256": manifest["manifest_sha256"], "package_name": manifest["package_name"],
            "relative_package": manifest["package_path"], "enable_ingame_tools": enable_ingame_tools,
            "launch_request_started_at": request_started_at,
            "launch_requested_at": datetime.now(timezone.utc).isoformat(),
            "stage": "launch_unobserved", "process": None, "process_observed": False,
            "capture_attempted": False, "exit_code": None, "exit_time": None,
            "compiler_exit_on_error": True, "compiler_failure_confirmed": False,
            "compiled_confirmed": False, "runtime_validation": "not_performed",
            "manifest_provenance": "requested_package_bytes", "outcome_unknown": True,
            **({"selected_mods": manifest["selected_mods"], "development_staging": stage,
                "manifest_provenance": "source_packages_verified_staged_bytes_at_launch"} if stage is not None else {})}
        return {"state": "launch_requested", "manifest_sha256": manifest["manifest_sha256"],
                "package_name": manifest["package_name"], "tool_result": response,
                "enable_ingame_tools": enable_ingame_tools,
                "compiled": False, "runtime_validation": "not_performed",
                "game_ready": False, "outcome_unknown": False,
                "launch_observation": copy.deepcopy(self._launch_observation),
                **({"development_staging": copy.deepcopy(stage),
                    "active_staging_packages": [{key: item[key] for key in
                        ("role", "source_package_path", "relative_package", "package_name", "manifest_sha256_at_launch")}
                        for item in stage["packages"]], "source_packages_modified": False,
                    "edit_target": "staged_copy_only", "hot_reload": "function_bodies_file_watch",
                    "staging_retention": "retain_for_review"} if stage is not None else {})}

    def stop(self, expected_process: dict) -> dict:
        with self._observation_lock:
            if self._closed:
                raise DevelopmentError("service_closed", "Контур наблюдения уже закрыт")
            return self._stop(expected_process)

    def _stop(self, expected_process: dict) -> dict:
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
        with self._observation_lock:
            if self._closed:
                raise DevelopmentError("service_closed", "Контур наблюдения уже закрыт")
            return self._load_native(expected_process, expected_dll_sha256)

    def _load_native(self, expected_process: dict, expected_dll_sha256: str) -> dict:
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
