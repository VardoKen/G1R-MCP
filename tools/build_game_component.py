"""Build the game-side G1R MCP folder without creating an archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
PACKAGE_NAME = "G1RMCPDevelopmentHelpers"
PROJECT_URL = "https://github.com/VardoKen/G1R-MCP"
HELPER_ROOT = f"development/helpers/{PACKAGE_NAME}"
TOOLS = (
    "game_process_common.ps1",
    "game_process_state.ps1",
    "start_g1r_development.ps1",
    "stop_g1r_development.ps1",
    "load_native.ps1",
    "native_load_result.ps1",
)
FILES = (
    "build/native/Release/G1R_MCP.dll",
    "build/native/Release/g1r_mcp_loader.exe",
    *(f"tools/{name}" for name in TOOLS),
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
DESCRIPTOR_METADATA = {
    "title": "G1R MCP",
    "projectWebsiteURL": PROJECT_URL,
    "description": (
        "Game-side component of G1R MCP for researching, developing, and debugging "
        "official Gothic 1 Remake mods. Includes the native bridge, its explicit "
        "loader, and AngelScript development helpers. Requires the external Python "
        "MCP server from the project repository."
    ),
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_game_component(destination: Path, repository_root: Path = ROOT) -> dict:
    repository = repository_root.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()

    def committed(path: str) -> bytes:
        return subprocess.check_output(
            ["git", "-C", str(repository), "show", f"{commit}:{path}"]
        )

    descriptor_path = f"{HELPER_ROOT}/GothicMod.json"
    original_descriptor = committed(descriptor_path)
    descriptor = json.loads(original_descriptor)
    if descriptor.get("name") != PACKAGE_NAME or descriptor.get("modVersion") != VERSION:
        raise ValueError("The committed helper name or version does not match this game component")
    descriptor.update(DESCRIPTOR_METADATA)
    descriptor_bytes = (json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    sources = [
        ("GothicMod.json", descriptor_path, "git_head_with_packaging_metadata",
         original_descriptor, descriptor_bytes),
    ]
    helper_script = f"Script/{PACKAGE_NAME}.as"
    script_bytes = committed(f"{HELPER_ROOT}/{helper_script}")
    sources.append((helper_script, f"{HELPER_ROOT}/{helper_script}", "git_head",
                    script_bytes, script_bytes))
    for path in FILES:
        raw = committed(path)
        sources.append((path, path, "git_head", raw, raw))
    readme = committed("docs/game-component.md")
    sources.append(("README.md", "docs/game-component.md", "git_head", readme, readme))

    target = destination.resolve()
    target.mkdir(parents=True, exist_ok=False)
    payload = target / PACKAGE_NAME
    payload.mkdir()
    inventory = []
    for path, source, source_kind, source_bytes, raw in sorted(sources):
        output = payload / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
        inventory.append({
            "path": path,
            "bytes": len(raw),
            "sha256": sha256(raw),
            "source": source,
            "source_kind": source_kind,
            "source_sha256": sha256(source_bytes),
        })
    manifest = {
        "schema_version": 1,
        "kind": "game_component_folder",
        "version": VERSION,
        "source_commit": commit,
        "project_url": PROJECT_URL,
        "package_directory": PACKAGE_NAME,
        "descriptor_metadata_overrides": DESCRIPTOR_METADATA,
        "file_count": len(inventory),
        "total_bytes": sum(item["bytes"] for item in inventory),
        "files": inventory,
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "version": VERSION,
        "payload": str(payload),
        "manifest": str(manifest_path),
        "source_commit": commit,
        "file_count": len(inventory),
        "total_bytes": manifest["total_bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=ROOT / f"artifacts/game-component-{VERSION}",
        help="New output directory containing the package folder and its separate manifest",
    )
    args = parser.parse_args()
    try:
        result = build_game_component(args.destination)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Game component build failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
