from __future__ import annotations

import html
import io
import re
import secrets
import zipfile
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".log"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".docx"}


class FileError(ValueError):
    pass


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise FileError("docx 文件无法解析") from exc
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def extract_text(name: str, raw: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise FileError("暂支持 txt / md / json / csv / docx；旧版 doc 请另存为 docx")
    text = _docx_text(raw) if suffix == ".docx" else _decode(raw)
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if not text:
        raise FileError("文件内容为空")
    return text[:MAX_TEXT_CHARS]


def save_file(name: str, mime: str, raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_FILE_BYTES:
        raise FileError(f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB")
    text = extract_text(name, raw)
    file_id = "file_" + secrets.token_hex(12)
    settings = get_settings()
    folder = settings.db_path.parent / "files"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower() or ".txt"
    (folder / (file_id + suffix)).write_bytes(raw)
    now = db.utcnow()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO files(id, name, mime, size, text, created_at) VALUES(?,?,?,?,?,?)",
            (file_id, Path(name).name[:200], mime[:120], len(raw), text, now),
        )
    return {
        "id": file_id,
        "name": Path(name).name,
        "mime": mime,
        "size": len(raw),
        "chars": len(text),
        "preview": text[:300],
    }


def load_texts(file_ids: list[str], max_total: int = 40_000) -> list[dict[str, Any]]:
    if not file_ids:
        return []
    with db.db() as conn:
        rows = conn.execute(
            "SELECT id, name, text FROM files WHERE id IN (%s)" % ",".join("?" for _ in file_ids),
            tuple(file_ids),
        ).fetchall()
    result = []
    used = 0
    for row in rows:
        remaining = max_total - used
        if remaining <= 0:
            break
        text = str(row["text"] or "")[:remaining]
        used += len(text)
        result.append({"id": row["id"], "name": row["name"], "text": text})
    return result
