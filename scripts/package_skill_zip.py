from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = root / "PACKAGE-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [
        str(item.get("path") or "")
        for item in manifest.get("files") or []
        if isinstance(item, dict) and item.get("path")
    ]
    entries = sorted(dict.fromkeys(entries))
    errors = []
    for relative in entries:
        path = root / relative
        item = next(
            value for value in manifest["files"]
            if isinstance(value, dict) and value.get("path") == relative
        )
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        if path.stat().st_size != int(item.get("size") or -1):
            errors.append(f"size mismatch: {relative}")
        if sha256_file(path) != str(item.get("sha256") or ""):
            errors.append(f"sha256 mismatch: {relative}")
    if errors:
        raise SystemExit("\n".join(errors))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in ["PACKAGE-MANIFEST.json", *entries]:
            source = root / relative
            info = zipfile.ZipInfo.from_file(source, arcname=relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    expected = ["PACKAGE-MANIFEST.json", *entries]
    if names != expected:
        raise SystemExit("zip verification failed: entry list mismatch")
    print(
        f"packaged {output} ({len(names)} entries, "
        f"{output.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
