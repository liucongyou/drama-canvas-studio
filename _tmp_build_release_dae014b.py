from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COMMIT = "dae014b"
ENTRY = "assets/index-DdlSQlY6.js"
ARCHIVE = ROOT / f"drama-canvas-release-{COMMIT}.tgz"
BACKEND = (
    "server.py",
    "canvas_store.py",
    "generation_outcome.py",
    "provider_protocols.py",
    "provider_catalog.py",
    "media_storage.py",
    "tos_storage.py",
)


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(data))


def main() -> None:
    index = (ROOT / "web-vimax" / "dist" / "index.html").read_text(
        encoding="utf-8"
    )
    if ENTRY not in index:
        raise RuntimeError("frontend bundle does not match release")
    with tarfile.open(ARCHIVE, "w:gz") as archive:
        for name in BACKEND:
            archive.add(ROOT / name, arcname=name)
        archive.add(ROOT / "web-vimax" / "dist", arcname="web-vimax/dist")
        add_bytes(archive, "RELEASE_COMMIT", f"{COMMIT}\n".encode("ascii"))
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    print(json.dumps({
        "archive": str(ARCHIVE),
        "sha256": digest,
        "entry": ENTRY,
        "size": ARCHIVE.stat().st_size,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
