"""Собрать проверяемый внутренний комплект моста с сохранённым layout.

Это рабочая папка для другого Windows-ПК, не публичный выпуск или installer.
Официальный Modkit, Python-окружение и игра не включаются; запуск/установка
игры не выполняются. .venv, журналы, кеши и локальные настройки исключены.
"""

from __future__ import annotations
import argparse
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from g1r_mcp.workspace import WorkspaceError, _checked, _destination_lock, _guard_directories, _read_bytes

MAX_FILES = 512
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
TOOLS = (
    "start_shared_mcp.ps1", "stop_shared_mcp.ps1",
    "game_process_common.ps1", "game_process_state.ps1", "start_g1r_development.ps1", "stop_g1r_development.ps1",
    "load_native.ps1", "native_load_result.ps1", "prepare_compile_probe.py", "prepare_object_probe.py",
    "prepare_class_probe.py", "prepare_name_probe.py", "prepare_patch_probe.py", "prepare_base_patch_probe.py",
    "probe_native_route.py", "probe_live_mcp.py", "probe_object_mcp.py", "probe_class_mcp.py",
    "probe_name_mcp.py", "probe_function_contract_mcp.py", "probe_metadata_mcp.py", "probe_development_mcp.py", "probe_cleanup.py", "prepare_bridge_bundle.py",
    "prepare_enum_probe.py", "probe_enum_mcp.py", "export_script_package.py",
    "prepare_lifecycle_probe.py", "probe_lifecycle_mcp.py", "prepare_pod_read_probe.py",
    "probe_array_mcp.py", "probe_struct_mcp.py",
    "prepare_world_lifecycle_probe.py", "probe_world_lifecycle_mcp.py",
)
FIXTURE_DIRS = ("angelscript_probe", "object_probe", "class_probe", "name_probe", "patch_probe", "enum_probe", "lifecycle_probe", "pod_read_probe", "world_lifecycle_probe")
SOURCE_FILES = ("__init__.py", "server.py", "native_client.py", "development.py", "packages.py", "patches.py", "sources.py", "source_functions.py", "source_identity.py", "game_environment.py", "workspace.py", "snapshots.py", "session_ownership.py", "compile_diagnostics.py")
FIXTURE_SCRIPTS = {"angelscript_probe": "G1RMCPProbe.as", "object_probe": "G1RMCPObjectProbe.as",
                   "class_probe": "G1RMCPClassProbe.as", "name_probe": "G1RMCPNameProbe.as", "patch_probe": "G1RMCPPatchProbe.as",
                   "enum_probe": "G1RMCPEnumProbe.as", "lifecycle_probe": "G1RMCPLifecycleProbe.as",
                   "pod_read_probe": "G1RMCPPODReadProbe.as", "world_lifecycle_probe": "G1RMCPWorldLifecycleProbe.as"}
DOCUMENTS = ("README.md", "pyproject.toml", "uv.lock")
BINARIES = ("build/native/Release/G1R_MCP.dll", "build/native/Release/g1r_mcp_loader.exe")


class BundleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _read(path: Path, boundary: Path) -> bytes:
    return _read_bytes(_checked(path, boundary), boundary, MAX_FILE_BYTES)


def _version(repository: Path) -> str:
    source = _read(repository / "src/g1r_mcp/__init__.py", repository).decode("utf-8-sig")
    parsed = ast.parse(source)
    versions = [node.value.value for node in parsed.body
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "__version__"
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if len(versions) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+-dev\.\d+", versions[0]):
        raise BundleError("invalid_version", "Комплект требует одну текущую техническую версию __version__")
    metadata = tomllib.loads(_read(repository / "pyproject.toml", repository).decode("utf-8-sig"))
    if metadata.get("project", {}).get("version") != versions[0]:
        raise BundleError("version_mismatch", "Версии Python-кода и pyproject.toml различаются")
    lock = tomllib.loads(_read(repository / "uv.lock", repository).decode("utf-8-sig"))
    packages = lock.get("package", [])
    local = [item for item in packages if isinstance(item, dict) and item.get("name") == "g1r-mcp"]
    sdk = [item for item in packages if isinstance(item, dict) and item.get("name") == "mcp"]
    if (len(local) != 1 or local[0].get("version") != versions[0].replace("-dev.", ".dev")
            or local[0].get("source") != {"editable": "."} or len(sdk) != 1 or sdk[0].get("version") != "2.3.0"
            or metadata.get("project", {}).get("dependencies") != ["mcp==2.3.0"]):
        raise BundleError("lock_mismatch", "uv.lock не соответствует версии, editable-корню или фиксированному MCP SDK")
    return versions[0]


def _compatibility(repository: Path, version: str, dll: bytes, loader: bytes) -> dict:
    source = _read(repository / "native/src/game_access.cpp", repository).decode("utf-8-sig")
    hashes = re.findall(r'constexpr\s+const\s+char\s*\*\s*kHash\s*=\s*"([0-9a-f]{64})"', source)
    if len(hashes) != 1 or version.encode("ascii") not in dll or hashes[0].encode("ascii") not in dll:
        raise BundleError("native_version_mismatch", "Собранная DLL не соответствует текущей версии или контракту EXE")
    for label, raw in (("DLL", dll), ("loader", loader)):
        if len(raw) < 64 or raw[:2] != b"MZ":
            raise BundleError("invalid_binary", f"{label} не является PE x64")
        offset = struct.unpack_from("<I", raw, 60)[0]
        if offset < 64 or offset + 6 > len(raw) or raw[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", raw, offset + 4)[0] != 0x8664:
            raise BundleError("invalid_binary", f"{label} не является PE x64")
    return {"platform": "Windows x64", "supported_game_exe_sha256": hashes[0],
            "supported_game_exe_name": "G1R/Binaries/Win64/G1R-Win64-Shipping.exe",
            "source": "native/src/game_access.cpp:kHash", "native_version_marker": version,
            "runtime_validation": "not_performed_by_packager"}


def _enumerate(repository: Path) -> list[str]:
    paths = list(DOCUMENTS) + ["tools/" + name for name in TOOLS] + list(BINARIES)

    def failure(error):
        raise BundleError("access_error", "Не удалось перечислить все разрешённые исходники комплекта")

    for root, allowed_suffix in ((repository / "src/g1r_mcp", {".py"}),
                                 *[(repository / "tests/fixtures" / name, {".json", ".as", ".patch"}) for name in FIXTURE_DIRS]):
        _checked(root, repository, directory=True)
        directory_count = 0
        for directory, directories, files in os.walk(root, followlinks=False, onerror=failure):
            directory_count += 1
            if directory_count > MAX_FILES:
                raise BundleError("bundle_limit", "Область исходников содержит слишком много каталогов")
            parent = _checked(Path(directory), repository, directory=True)
            directories[:] = [name for name in directories if name != "__pycache__"]
            for name in directories:
                _checked(parent / name, repository, directory=True)
            for name in files:
                if len(paths) >= MAX_FILES:
                    raise BundleError("bundle_limit", "Исходники превышают предел числа файлов")
                path = _checked(parent / name, repository)
                if not path.is_file():
                    raise BundleError("unsafe_file", "В разрешённой области есть не обычный файл")
                if path.suffix.lower() in allowed_suffix:
                    paths.append(path.relative_to(repository).as_posix())
    if len(paths) > MAX_FILES or len(set(paths)) != len(paths):
        raise BundleError("bundle_limit", "Комплект превышает предел или содержит повторные пути")
    return sorted(paths)


def _target(destination: Path, repository: Path) -> Path:
    artifacts = _checked(repository / "artifacts", repository, directory=True)
    target = Path(os.path.abspath(destination))
    if target == artifacts or not target.is_relative_to(artifacts):
        raise BundleError("unsafe_path", "Для комплекта нужен новый отдельный каталог внутри artifacts")
    _checked(target.parent, artifacts, directory=True)
    if os.path.lexists(target):
        raise BundleError("already_exists", "Каталог назначения уже существует")
    return target


def _cleanup(stage: Path, parent: Path, own_files: dict, own_directories: dict) -> None:
    try:
        with _guard_directories(stage, parent):
            for relative, identity in own_directories.items():
                path = _checked(stage if relative == "." else stage / relative, stage, directory=True)
                if _identity(path) != identity:
                    return
            for relative, (identity, digest) in own_files.items():
                path = _checked(stage / relative, stage)
                with _guard_directories(path.parent, stage):
                    if _identity(path) == identity and _sha(_read(path, stage)) == digest:
                        path.unlink()
            for relative, identity in sorted(own_directories.items(), key=lambda item: item[0].count("/"), reverse=True):
                if relative == ".":
                    continue
                path = _checked(stage / relative, stage, directory=True)
                if _identity(path) == identity and not any(path.iterdir()):
                    path.rmdir()
        if _identity(_checked(stage, parent, directory=True)) == own_directories.get(".") and not any(stage.iterdir()):
            stage.rmdir()
    except (WorkspaceError, OSError):
        pass


def _file_set(root: Path) -> set[str]:
    paths = set()

    def failure(error):
        raise BundleError("copy_changed", "Не удалось проверить полный состав комплекта")

    directory_count = 0
    for directory, directories, files in os.walk(root, followlinks=False, onerror=failure):
        directory_count += 1
        if directory_count > MAX_FILES or len(paths) > MAX_FILES + 2:
            raise BundleError("bundle_limit", "Подготовленный комплект неожиданно увеличился")
        parent = _checked(Path(directory), root, directory=True)
        for name in directories:
            _checked(parent / name, root, directory=True)
        for name in files:
            if len(paths) >= MAX_FILES + 2:
                raise BundleError("bundle_limit", "Комплект превышает предел числа файлов")
            path = _checked(parent / name, root)
            if not path.is_file():
                raise BundleError("unsafe_file", "Комплект содержит не обычный файл")
            paths.add(path.relative_to(root).as_posix())
    return paths


def _directory_set(root: Path) -> set[str]:
    directories = {"."}
    for directory, children, _ in os.walk(root, followlinks=False,
                                          onerror=lambda _: (_ for _ in ()).throw(BundleError("copy_changed", "Каталог комплекта недоступен"))):
        parent = _checked(Path(directory), root, directory=True)
        for name in children:
            path = _checked(parent / name, root, directory=True)
            directories.add(path.relative_to(root).as_posix())
            if len(directories) > MAX_FILES:
                raise BundleError("bundle_limit", "Комплект содержит слишком много каталогов")
    return directories


def _instructions(version: str) -> bytes:
    text = f"""# Внутренний комплект G1R MCP {version}

Комплект сохраняет структуру рабочей базы; это не установщик и не публичный выпуск.
Проверка не запускает игру и не загружает DLL.

1. Поместить всю папку в выбранный рабочий каталог Windows x64; игру и официальный
   G1R-Modkit оставить отдельно. Не переносить старую `.venv`.
2. Из корня выполнить `python -B tools/prepare_bridge_bundle.py --verify-root .`.
   `-B` запрещает создание __pycache__ внутри проверяемой папки.
3. Установить Python 3.11+ и uv. Разместить окружение рядом с комплектом:
   `$env:UV_PROJECT_ENVIRONMENT = "<отдельный абсолютный путь к окружению>"`,
   `$env:PYTHONDONTWRITEBYTECODE = "1"`, затем выполнить `uv sync --locked`.
   Окружение не создавать внутри комплекта: строгая проверка отвергает новые файлы.
4. Для всех чатов Codex запустить один общий сервер отдельным процессом:
   `<окружение>\\Scripts\\python.exe -B -m g1r_mcp.server --transport streamable-http
   --host 127.0.0.1 --port 27124 --modkit-root <официальный Modkit>
   --workspace-root <рабочие пакеты> --game-root <установленная игра>`.
   В config.toml Codex задать `[mcp_servers.g1r_mcp]` и
   `url = "http://127.0.0.1:27124/mcp"`; не задавать command/args для каждого чата.
   Сервер не запускает игру, все чаты разделяют один native TCP-владелец.
   Для отдельного stdio-клиента остаётся транспорт по умолчанию без HTTP-параметров.
   Пути задать абсолютными; не использовать значения из другого ПК.
   Скрипты start_shared_mcp.ps1/stop_shared_mcp.ps1 рассчитаны на рабочую копию
   с локальной `.venv` и соседним G1R-Modkit, не на неизменяемый комплект
   с вынесенным окружением. В последнем случае использовать прямую команду выше.
5. Проверить EXE игры
   SHA-256 против manifest.json до использования DLL. Загрузка выполняется только
   явным инструментом development/native_load; комплект сам ничего не устанавливает.

Для рабочего цикла доступны MCP-команды g1r_development_recipe/processes/start/stop,
g1r_native_load/status/enable, поиск и типизированные вызовы, редактура пакетов,
снимки и наблюдения. Запуск, остановка и загрузка DLL выполняются явными запросами.

Python-зависимости загружаются `uv sync` по uv.lock. Требуется установленный
Microsoft Visual C++ Redistributable x64. DLL и loader уже собраны;
компилятор C++ для запуска готового комплекта не нужен. Сборочные исходники native
не входят в этот минимальный комплект; повторная сборка выполняется в Git-базе.

Компиляция .as происходит в игре; hot reload подтверждён только для тел функций.
Неподтверждённые compiler API и применение ScriptPatches не считаются готовыми.
Собственные проверочные пакеты готовятся отдельно из tests/fixtures в artifacts;
рабочие моды пользователя и результаты прошлых экспериментов не включены.

Для проверки полного цикла компиляции создать отдельную полную рабочую копию
комплекта. Исходную папку сохранить для проверки manifest; рабочая копия получает
собственный artifacts и после этого не проходит проверку исходного manifest.
В PowerShell указать абсолютные пути: существующий комплект, новый соседний
каталог рабочей копии, установленное окружение, официальный Modkit и игру.

```powershell
$bundlePath = "<абсолютный путь к исходному комплекту>"
$workingPath = "<абсолютный путь к новой соседней рабочей копии>"
$pythonPath = "<абсолютный путь к окружению>\\Scripts\\python.exe"
$modkitPath = "<абсолютный путь к официальному G1R-Modkit>"
$gamePath = "<абсолютный путь к установленной игре>"
$env:PYTHONDONTWRITEBYTECODE = "1"
$bundlePath = (Resolve-Path -LiteralPath $bundlePath).ProviderPath
$workingPath = [IO.Path]::GetFullPath($workingPath)
if (Test-Path -LiteralPath $workingPath) {{ throw "Рабочая копия уже существует" }}
if ([IO.Path]::GetDirectoryName($workingPath) -ne [IO.Path]::GetDirectoryName($bundlePath)) {{ throw "Выберите соседний каталог" }}
& $pythonPath -B "$bundlePath\\tools\\prepare_bridge_bundle.py" --verify-root $bundlePath
if ($LASTEXITCODE -ne 0) {{ throw "Исходный комплект не прошёл проверку" }}
Copy-Item -LiteralPath $bundlePath -Destination $workingPath -Recurse
$env:UV_PROJECT_ENVIRONMENT = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($pythonPath))
uv sync --locked --project $workingPath
if ($LASTEXITCODE -ne 0) {{ throw "Окружение рабочей копии не подготовлено" }}
& $pythonPath -B "$workingPath\\tools\\prepare_compile_probe.py" --workspace-root "$workingPath\\artifacts\\compile-probe-baseline" --phase baseline
if ($LASTEXITCODE -ne 0) {{ throw "Подготовка baseline не выполнена" }}
& $pythonPath -B "$workingPath\\tools\\probe_development_mcp.py" --modkit-root $modkitPath --workspace-root $workingPath --game-root $gamePath --relative-package "artifacts/compile-probe-baseline/G1RMCPProbe" --expected-value 1
```

Последняя команда явно запускает собственную игровую сессию, загружает DLL,
проверяет baseline-метод ReadProbeValue и закрывает эту сессию. Выполнять её
только при разрешённом запуске игры. Ожидается значение 1; локальная reload-проба
рабочей базы с ожидаемым значением 2 в комплект не включена.
"""
    return text.encode("utf-8")


def prepare_bundle(destination: Path, *, repository_root: Path = ROOT) -> dict:
    repository = _checked(repository_root, directory=True)
    target = _target(destination, repository)
    version = _version(repository)
    selected = _enumerate(repository)
    raw_files = {}
    total = 0
    for relative in selected:
        raw = _read(repository / relative, repository)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise BundleError("bundle_limit", "Комплект превышает предел 64 МиБ")
        raw_files[relative] = raw
    compatibility = _compatibility(repository, version, raw_files[BINARIES[0]], raw_files[BINARIES[1]])
    compatibility_source = _sha(_read(repository / "native/src/game_access.cpp", repository))
    raw_files["README.md"] = _instructions(version)
    raw_files["НАЧАТЬ.md"] = raw_files["README.md"]
    # Источник проверяется повторно до записи; поздние изменения отказывают.
    if selected != _enumerate(repository) or any(_sha(_read(repository / path, repository)) != _sha(raw_files[path]) for path in selected if path != "README.md"):
        raise BundleError("source_changed", "Исходники или бинарники изменились во время подготовки")
    if _sha(_read(repository / "native/src/game_access.cpp", repository)) != compatibility_source:
        raise BundleError("source_changed", "Контракт нативной совместимости изменился во время подготовки")
    files = [{"path": relative, "bytes": len(raw), "sha256": _sha(raw)} for relative, raw in sorted(raw_files.items())]
    manifest = {"schema_version": 1, "kind": "internal_bridge_folder", "version": version,
                "compatibility": compatibility, "files": files,
                "file_count": len(files), "total_bytes": sum(item["bytes"] for item in files),
                "includes": ["python_sources", "locked_dependencies", "development_tools", "probe_fixtures", "prebuilt_native_bridge", "instructions"],
                "excludes": ["virtual_environment", "official_modkit", "game_files", "game_caches", "user_mods", "saved_games", "logs", "local_mcp_settings"],
                "runtime_validation": "not_performed_by_packager", "installed": False}
    manifest["compatibility_source_sha256"] = compatibility_source
    raw_files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    stage = None
    own_files = {}
    own_directories = {}
    with _destination_lock(target), _guard_directories(target.parent, repository / "artifacts"):
        try:
            if os.path.lexists(target):
                raise BundleError("already_exists", "Каталог комплекта появился до записи")
            stage = Path(tempfile.mkdtemp(prefix=".g1r-bridge-bundle-", dir=target.parent))
            own_directories["."] = _identity(stage)
            with _guard_directories(stage, target.parent), contextlib.ExitStack() as guards:
                for relative, raw in sorted(raw_files.items()):
                    file = stage / relative
                    missing = []
                    parent = file.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    for directory in reversed(missing):
                        _checked(directory.parent, stage, directory=True)
                        directory.mkdir()
                        own_directories[directory.relative_to(stage).as_posix()] = _identity(directory)
                        guards.enter_context(_guard_directories(directory, stage))
                    with _guard_directories(file.parent, stage):
                        partial = None
                        try:
                            with file.open("xb") as stream:
                                info = os.fstat(stream.fileno())
                                partial = (info.st_dev, info.st_ino)
                                stream.write(raw)
                            own_files[relative] = (_identity(_checked(file, stage)), _sha(raw))
                        except OSError:
                            if partial is not None:
                                checked = _checked(file, stage)
                                if _identity(checked) == partial and raw.startswith(_read(checked, stage)):
                                    checked.unlink()
                            raise
                if _file_set(stage) != set(raw_files) or _directory_set(stage) != set(own_directories):
                    raise BundleError("copy_changed", "Комплект содержит посторонние файлы")
                for relative, (identity, digest) in own_files.items():
                    checked = _checked(stage / relative, stage)
                    if _identity(checked) != identity or _sha(_read(checked, stage)) != digest:
                        raise BundleError("copy_changed", "Подготовленный файл комплекта изменился")
                verified = verify_bundle(stage)
            if os.path.lexists(target):
                raise BundleError("already_exists", "Каталог комплекта появился до публикации")
            stage.rename(target)
            stage = None
            return {"destination": str(target), "version": version, "file_count": verified["file_count"],
                    "total_bytes": verified["total_bytes"], "manifest_sha256": _sha(raw_files["manifest.json"]),
                    "supported_game_exe_sha256": compatibility["supported_game_exe_sha256"],
                    "verified_files": True, "installed": False, "game_launched": False,
                    "runtime_validation": "not_performed_by_packager"}
        finally:
            if stage is not None:
                _cleanup(stage, target.parent, own_files, own_directories)


def verify_bundle(bundle_root: Path) -> dict:
    root = _checked(bundle_root, directory=True)
    raw = _read(root / "manifest.json", root)
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise BundleError("invalid_manifest", "Manifest комплекта не является корректным JSON") from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("kind") != "internal_bridge_folder":
        raise BundleError("invalid_manifest", "Неподдержанный manifest комплекта")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise BundleError("invalid_manifest", "Неверный список файлов комплекта")
    expected = {"manifest.json"}
    expected_directories = {"."}
    total = 0
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleError("invalid_manifest", "Неверная запись файла")
        path = entry["path"]
        if path.startswith(("/", "\\")) or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")) or ":" in path:
            raise BundleError("invalid_manifest", "Путь manifest выходит за пределы комплекта")
        if path in expected:
            raise BundleError("invalid_manifest", "Manifest содержит повторный путь")
        expected.add(path)
        parent = Path(path).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent
        content = _read(root / path, root)
        if type(entry.get("bytes")) is not int or entry["bytes"] != len(content) or entry.get("sha256") != _sha(content):
            raise BundleError("integrity_mismatch", "Хеш или размер файла комплекта не совпадает")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise BundleError("bundle_limit", "Комплект превышает предел объёма")
    if (_file_set(root) != expected or _directory_set(root) != expected_directories
            or manifest.get("file_count") != len(files) or manifest.get("total_bytes") != total):
        raise BundleError("integrity_mismatch", "Полный состав комплекта отличается от manifest")
    required = set(DOCUMENTS) | {"tools/" + name for name in TOOLS} | set(BINARIES) | {"НАЧАТЬ.md"}
    required |= {"src/g1r_mcp/" + name for name in SOURCE_FILES}
    required |= {f"tests/fixtures/{name}/GothicMod.json" for name in FIXTURE_DIRS}
    required |= {f"tests/fixtures/{name}/Script/{script}" for name, script in FIXTURE_SCRIPTS.items()}
    required.add("tests/fixtures/patch_probe/ScriptPatches/Marker.patch")
    if not required.issubset(expected):
        raise BundleError("incomplete_bundle", "В комплекте отсутствует обязательный runtime-файл")
    version = _version(root)
    compatibility = manifest.get("compatibility")
    if (manifest.get("version") != version or not isinstance(compatibility, dict)
            or compatibility.get("native_version_marker") != version
            or compatibility.get("platform") != "Windows x64"
            or not isinstance(compatibility.get("supported_game_exe_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", compatibility["supported_game_exe_sha256"])):
        raise BundleError("invalid_manifest", "Версия или контракт совместимости комплекта некорректны")
    dll = _read(root / BINARIES[0], root)
    loader = _read(root / BINARIES[1], root)
    game_hash = compatibility["supported_game_exe_sha256"]
    if version.encode("ascii") not in dll or game_hash.encode("ascii") not in dll:
        raise BundleError("native_version_mismatch", "DLL не соответствует версии или хешу игры из manifest")
    for content in (dll, loader):
        if len(content) < 64 or content[:2] != b"MZ":
            raise BundleError("invalid_binary", "Бинарник комплекта не является PE x64")
        offset = struct.unpack_from("<I", content, 60)[0]
        if offset < 64 or offset + 6 > len(content) or content[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", content, offset + 4)[0] != 0x8664:
            raise BundleError("invalid_binary", "Бинарник комплекта не является PE x64")
    return {"version": manifest.get("version"), "file_count": len(files), "total_bytes": total,
            "verified_files": True, "runtime_validation": "not_performed_by_packager"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовить или проверить внутренний переносимый комплект G1R MCP")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--destination", type=Path, help="Новый каталог внутри artifacts текущей Git-базы")
    group.add_argument("--verify-root", type=Path, help="Проверить полные хеши распакованного комплекта без запуска игры")
    args = parser.parse_args()
    try:
        result = prepare_bundle(args.destination) if args.destination else verify_bundle(args.verify_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BundleError, WorkspaceError, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"code": getattr(error, "code", "bundle_failed"), "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
