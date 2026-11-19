from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rebuild(
    root: Path,
    *,
    version: str,
    package_revision: str,
    release_date: str,
    output: Path | None = None,
) -> dict:
    manifest_path = root / "PACKAGE-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous_paths = {
        str(item.get("path") or "")
        for item in manifest.get("files") or []
        if isinstance(item, dict) and item.get("path")
    }
    package_roots = {
        Path(value).parts[0]
        for value in previous_paths
        if len(Path(value).parts) > 1
    }
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == "PACKAGE-MANIFEST.json":
            continue
        if "__pycache__" in relative.parts or ".pytest_cache" in relative.parts:
            continue
        if path.suffix in {".pyc", ".pyo"} or ".bak" in path.name:
            continue
        if (
            len(relative.parts) > 1
            and relative.parts[0] not in package_roots
        ):
            continue
        if (
            len(relative.parts) == 1
            and relative.as_posix() not in previous_paths
        ):
            continue
        files.append(
            {
                "path": relative.as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest.update(
        {
            "version": version,
            "releaseDate": release_date,
            "packageRevision": package_revision,
            "fileCount": len(files),
            "files": files,
        }
    )
    output_path = output or manifest_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".json.next")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp_path, output_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--release-date", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = rebuild(
        args.root.resolve(),
        version=args.version,
        package_revision=args.revision,
        release_date=args.release_date,
        output=args.output.resolve() if args.output else None,
    )
    print(f"manifest rebuilt: {manifest['fileCount']} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
