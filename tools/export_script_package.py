"""Export only an official script package to a verifiable ZIP.

Sources are not changed. Config/Content/Story and unrelated files are rejected
to prevent silent content loss. The ZIP and manifest are published together
in a new artifacts directory. Compilation, installation, and patch application are not performed.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from g1r_mcp.packages import PackageError, validate_package, MAX_DESCRIPTOR_BYTES
from g1r_mcp.workspace import (PackageWorkspace, WorkspaceError, _checked, _parts,
                               _destination_lock, _guard_directories, _read_bytes)

MAX_FILES = 10_000
MAX_DIRECTORIES = 10_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class ExportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _allowed(relative: str) -> tuple[str, ...]:
    parts = _parts(relative)
    if parts == ("GothicMod.json",):
        return parts
    if len(parts) >= 2 and parts[0] == "Script" and Path(parts[-1]).suffix.lower() == ".as":
        return parts
    if len(parts) == 2 and parts[0] == "ScriptPatches" and Path(parts[-1]).suffix.lower() == ".patch":
        return parts
    raise ExportError("unsupported_entry", "Script export accepts only GothicMod.json, Script/**/*.as, and ScriptPatches/*.patch")


def _source_files(workspace: PackageWorkspace, relative_package: str) -> tuple[Path, dict, dict[str, bytes]]:
    package, relative = workspace._package(relative_package)
    validation = validate_package(workspace.root, relative)
    if not validation["valid_structure"]:
        raise ExportError("invalid_package", "The official package failed structural validation")
    name = validation["metadata"]["name"]
    if _parts(name) != (name,):
        raise ExportError("invalid_package_name", "The package name must be a single valid Windows path component")
    files = {}
    directories = 0
    folded = set()

    def failure(error):
        raise ExportError("access_error", "Failed to enumerate the entire script package")

    with _guard_directories(package, workspace.root):
        for directory, children, names in os.walk(package, followlinks=False, onerror=failure):
            directories += 1
            if directories > MAX_DIRECTORIES:
                raise ExportError("export_limit", "The package exceeds the directory limit")
            parent = _checked(Path(directory), package, directory=True)
            for child in children:
                path = workspace._unprotected(_checked(parent / child, package, directory=True))
                parts = path.relative_to(package).parts
                _parts(path.relative_to(package).as_posix())
                if not parts or parts[0] not in {"Script", "ScriptPatches"} or (parts[0] == "ScriptPatches" and len(parts) != 1):
                    raise ExportError("unsupported_entry", "The package contains a directory outside script export")
            for filename in names:
                if len(files) >= MAX_FILES:
                    raise ExportError("export_limit", "The package exceeds the file limit")
                path = workspace._unprotected(_checked(parent / filename, package))
                relative_path = path.relative_to(package).as_posix()
                parts = _allowed(relative_path)
                if relative_path.casefold() in folded:
                    raise ExportError("duplicate_path", "The package contains duplicate Windows paths")
                folded.add(relative_path.casefold())
                raw = _read_bytes(path, package, MAX_DESCRIPTOR_BYTES if len(parts) == 1 else None)
                try:
                    text = raw.decode("utf-8-sig")
                    if "\0" in text:
                        raise ValueError()
                except (UnicodeError, ValueError):
                    raise ExportError("invalid_encoding", "Export sources must be UTF-8 without NUL") from None
                files[relative_path] = raw
    if not any(path.startswith(("Script/", "ScriptPatches/")) for path in files):
        raise ExportError("empty_package", "The package has no sources or patches to export")
    if _descriptor_metadata(files.get("GothicMod.json", b"")) != validation["metadata"]:
        raise ExportError("source_changed", "The descriptor changed between validation and reading")
    return package, validation["metadata"], files


def _destination(path: Path, workspace: PackageWorkspace) -> Path:
    artifacts = workspace._unprotected(_checked(workspace.root / "artifacts", workspace.root, directory=True), forbid_ancestor=True)
    target = Path(os.path.abspath(path))
    if target == artifacts or not target.is_relative_to(artifacts):
        raise ExportError("unsafe_path", "Export requires a new directory within artifacts in the selected workspace")
    _checked(target.parent, artifacts, directory=True)
    workspace._unprotected(target, forbid_ancestor=True)
    if os.path.lexists(target):
        raise ExportError("already_exists", "The export directory already exists")
    return target


def _archive_bytes(files: dict[str, bytes], name: str) -> bytes:
    import io
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for relative, raw in sorted(files.items()):
            entry = zipfile.ZipInfo(name + "/" + relative, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, raw)
    return stream.getvalue()


def _cleanup(stage: Path, parent: Path, own_files: dict, stage_identity: tuple[int, int]) -> None:
    try:
        with _guard_directories(stage, parent):
            if _identity(_checked(stage, parent, directory=True)) != stage_identity:
                return
            for name, (identity, digest) in own_files.items():
                path = _checked(stage / name, stage)
                if _identity(path) == identity and _sha(_read_bytes(path, stage, None)) == digest:
                    path.unlink()
        if not any(stage.iterdir()):
            stage.rmdir()
    except (WorkspaceError, OSError):
        pass


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _descriptor_metadata(raw: bytes) -> dict:
    try:
        descriptor = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_json,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ExportError("invalid_descriptor", "The export descriptor is not valid UTF-8 JSON") from None
    if not isinstance(descriptor, dict) or any(not isinstance(descriptor.get(field), str) or not descriptor[field].strip()
                                               for field in ("name", "title", "author", "modVersion")):
        raise ExportError("invalid_descriptor", "The export descriptor is missing required fields")
    metadata = {field: descriptor[field] for field in ("name", "title", "author", "modVersion")}
    for field in ("description", "projectWebsiteURL"):
        if field in descriptor:
            if not isinstance(descriptor[field], str):
                raise ExportError("invalid_descriptor", "An optional descriptor field must be a string")
            metadata[field] = descriptor[field]
    if "loadOrder" in descriptor:
        if descriptor["loadOrder"] not in {"first", "early", "normal", "late", "last"}:
            raise ExportError("invalid_descriptor", "Invalid loadOrder")
        metadata["loadOrder"] = descriptor["loadOrder"]
    if "dependencies" in descriptor:
        dependencies = descriptor["dependencies"]
        if not isinstance(dependencies, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                                                    or not item["name"].strip() or not isinstance(item.get("requiredVersion"), str)
                                                    for item in dependencies):
            raise ExportError("invalid_descriptor", "Invalid descriptor dependencies")
        metadata["dependencies"] = [{"name": item["name"], "requiredVersion": item["requiredVersion"]} for item in dependencies]
    return metadata


def export_package(workspace_root: Path, relative_package: str, destination: Path, *,
                   modkit_root: Path, game_root: Path) -> dict:
    workspace = PackageWorkspace(workspace_root, protected_roots=(modkit_root, game_root))
    target = _destination(destination, workspace)
    package, metadata, files = _source_files(workspace, relative_package)
    if target.is_relative_to(package) or package.is_relative_to(target):
        raise ExportError("unsafe_path", "The export directory overlaps the source package")
    _, final_metadata, final_files = _source_files(workspace, relative_package)
    if final_metadata != metadata or final_files != files:
        raise ExportError("source_changed", "The script package changed while reading")
    name = metadata["name"]
    archive_raw = _archive_bytes(files, name)
    archive_name = name + ".zip"
    entries = [{"path": path, "bytes": len(raw), "sha256": _sha(raw)} for path, raw in sorted(files.items())]
    manifest = {"schema_version": 1, "kind": "official_script_package_export", "package_name": name,
                "mod_version": metadata["modVersion"], "archive": {"filename": archive_name, "bytes": len(archive_raw), "sha256": _sha(archive_raw)},
                "files": entries, "file_count": len(entries), "total_source_bytes": sum(item["bytes"] for item in entries),
                "byte_preservation": True, "compiled": False, "installed": False, "runtime_validation": "not_performed"}
    manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise ExportError("export_limit", "The export manifest exceeds the limit")
    stage = None
    stage_identity = None
    owned = {}
    with _destination_lock(target), _guard_directories(target.parent, workspace.root):
        try:
            if os.path.lexists(target):
                raise ExportError("already_exists", "The export directory appeared before writing")
            stage = Path(tempfile.mkdtemp(prefix=".g1r-script-export-", dir=target.parent))
            stage_identity = _identity(stage)
            with _guard_directories(stage, target.parent):
                for filename, raw in ((archive_name, archive_raw), ("manifest.json", manifest_raw)):
                    file = stage / filename
                    partial = None
                    try:
                        with file.open("xb") as stream:
                            info = os.fstat(stream.fileno())
                            partial = (info.st_dev, info.st_ino)
                            stream.write(raw)
                        owned[filename] = (_identity(file), _sha(raw))
                    except OSError:
                        if partial is not None:
                            checked = _checked(file, stage)
                            current = _read_bytes(checked, stage, None)
                            if _identity(checked) == partial and raw.startswith(current):
                                checked.unlink()
                        raise
                verify_export(stage)
                for filename, (identity, digest) in owned.items():
                    checked = _checked(stage / filename, stage)
                    if _identity(checked) != identity or _sha(_read_bytes(checked, stage, None)) != digest:
                        raise ExportError("copy_changed", "The prepared ZIP or manifest changed before publication")
            if os.path.lexists(target):
                raise ExportError("already_exists", "The export directory appeared before publication")
            stage.rename(target)
            stage = None
            return {"destination": str(target), "archive": archive_name, "archive_sha256": _sha(archive_raw),
                    "manifest_sha256": _sha(manifest_raw), "file_count": len(entries), "total_source_bytes": manifest["total_source_bytes"],
                    "compiled": False, "installed": False, "runtime_validation": "not_performed"}
        finally:
            if stage is not None:
                _cleanup(stage, target.parent, owned, stage_identity)


def verify_export(export_root: Path) -> dict:
    root = _checked(export_root, directory=True)
    raw_manifest = _read_bytes(_checked(root / "manifest.json", root), root, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_unique_json,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError):
        raise ExportError("invalid_manifest", "The export manifest is not valid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("kind") != "official_script_package_export":
        raise ExportError("invalid_manifest", "Unsupported export manifest")
    if (manifest.get("byte_preservation") is not True or manifest.get("compiled") is not False
            or manifest.get("installed") is not False or manifest.get("runtime_validation") != "not_performed"):
        raise ExportError("invalid_manifest", "The export manifest contains unverified runtime claims")
    name = manifest.get("package_name")
    if not isinstance(name, str) or _parts(name) != (name,):
        raise ExportError("invalid_manifest", "Invalid package root name")
    archive_info = manifest.get("archive")
    if not isinstance(archive_info, dict) or archive_info.get("filename") != name + ".zip":
        raise ExportError("invalid_manifest", "Invalid ZIP name")
    archive_path = _checked(root / archive_info["filename"], root)
    archive_raw = _read_bytes(archive_path, root, None)
    if type(archive_info.get("bytes")) is not int or len(archive_raw) != archive_info["bytes"] or _sha(archive_raw) != archive_info.get("sha256"):
        raise ExportError("integrity_mismatch", "The ZIP hash does not match the manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ExportError("invalid_manifest", "Invalid source list")
    expected = {}
    folded = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ExportError("invalid_manifest", "Invalid source entry")
        path = item["path"]
        _allowed(path)
        if (path.casefold() in folded or type(item.get("bytes")) is not int or item["bytes"] < 0
                or path == "GothicMod.json" and item["bytes"] > MAX_DESCRIPTOR_BYTES):
            raise ExportError("invalid_manifest", "Duplicate source path or invalid source size")
        folded.add(path.casefold())
        expected[name + "/" + path] = item
        total += item["bytes"]
    if name + "/GothicMod.json" not in expected or not any(path.startswith((name + "/Script/", name + "/ScriptPatches/")) for path in expected):
        raise ExportError("incomplete_package", "The ZIP is missing the descriptor and script component")
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
            entries = archive.infolist()
            if len(entries) != len(expected) or len(entries) > MAX_FILES:
                raise ExportError("integrity_mismatch", "The ZIP entry count differs from the manifest")
            seen = set()
            descriptor = None
            for entry in entries:
                if entry.filename.casefold() in seen or entry.filename not in expected or entry.flag_bits & 1 or entry.compress_type != zipfile.ZIP_STORED:
                    raise ExportError("unsafe_archive", "The ZIP contains duplicate, encrypted, or invalid entries")
                seen.add(entry.filename.casefold())
                mode = (entry.external_attr >> 16) & 0xFFFF
                if (mode and not stat.S_ISREG(mode)) or entry.is_dir() or entry.file_size != expected[entry.filename]["bytes"]:
                    raise ExportError("unsafe_archive", "The ZIP contains a non-regular file or an invalid size")
                with archive.open(entry) as stream:
                    content = stream.read()
                if len(content) != entry.file_size or _sha(content) != expected[entry.filename]["sha256"]:
                    raise ExportError("integrity_mismatch", "A ZIP source differs from the manifest")
                if "\0" in content.decode("utf-8-sig"):
                    raise ExportError("unsafe_archive", "A ZIP source contains NUL")
                if entry.filename == name + "/GothicMod.json":
                    descriptor = _descriptor_metadata(content)
            if not isinstance(descriptor, dict) or descriptor.get("name") != name or descriptor.get("modVersion") != manifest.get("mod_version"):
                raise ExportError("integrity_mismatch", "The ZIP descriptor does not match the manifest")
    except (zipfile.BadZipFile, UnicodeError, ValueError):
        raise ExportError("unsafe_archive", "The ZIP or its UTF-8 sources are corrupted") from None
    actual = {_checked(path, root).name for path in root.iterdir()}
    if actual != {"manifest.json", name + ".zip"} or manifest.get("file_count") != len(expected) or manifest.get("total_source_bytes") != total:
        raise ExportError("integrity_mismatch", "The export directory contains unrelated files or invalid totals")
    return {"package_name": name, "file_count": len(expected), "archive_sha256": _sha(archive_raw),
            "verified_files": True, "compiled": False, "installed": False, "runtime_validation": "not_performed"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or verify an official script package without installing it in the game")
    parser.add_argument("--verify-root", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--package")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--modkit-root", type=Path)
    parser.add_argument("--game-root", type=Path)
    args = parser.parse_args()
    try:
        if args.verify_root:
            if any((args.workspace_root, args.package, args.destination, args.modkit_root, args.game_root)):
                parser.error("--verify-root must be used alone")
            result = verify_export(args.verify_root)
        else:
            if not all((args.workspace_root, args.package, args.destination, args.modkit_root, args.game_root)):
                parser.error("Export requires workspace-root, package, destination, modkit-root, and game-root")
            result = export_package(args.workspace_root, args.package, args.destination,
                                    modkit_root=args.modkit_root, game_root=args.game_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ExportError, WorkspaceError, PackageError, OSError, UnicodeError) as error:
        print(json.dumps({"code": getattr(error, "code", "export_failed"), "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
