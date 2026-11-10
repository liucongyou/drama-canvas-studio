from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import get_settings
from app.db import db, utcnow

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BUILTIN = Path(__file__).resolve().parent.parent / "skills" / "demo"


class SkillError(ValueError):
    pass


def _safe_members(zf: zipfile.ZipFile, max_files: int) -> list[zipfile.ZipInfo]:
    members = [item for item in zf.infolist() if not item.is_dir()]
    if len(members) > max_files:
        raise SkillError(f"技能包文件数超过上限 {max_files}")
    for item in members:
        name = item.filename.replace("\\", "/")
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            raise SkillError(f"技能包包含非法路径：{item.filename}")
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise SkillError("技能包不能包含符号链接")
    return members


def _find_manifest(root: Path) -> tuple[Path, dict[str, Any], str]:
    candidates = [
        root / "skill.json",
        root / "PACKAGE-MANIFEST.json",
    ]
    for child in sorted(root.iterdir()):
        if child.is_dir():
            candidates.append(child / "skill.json")
            candidates.append(child / "PACKAGE-MANIFEST.json")
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise SkillError(f"manifest 不是有效 JSON：{path.name}") from exc
            if isinstance(data, dict):
                return path, data, path.name
    raise SkillError("技能包缺少 skill.json 或 PACKAGE-MANIFEST.json")


def _normalize_manifest(manifest: dict[str, Any], source_name: str) -> dict[str, Any]:
    skill = manifest.get("skill") if isinstance(manifest.get("skill"), dict) else manifest
    skill_id = str(skill.get("id") or manifest.get("id") or manifest.get("package") or "").strip()
    version = str(skill.get("version") or manifest.get("version") or "").strip()
    name = str(skill.get("name") or manifest.get("name") or manifest.get("package") or skill_id).strip()
    entry = str(skill.get("entry") or manifest.get("entry") or "").strip()
    if not entry:
        skills = manifest.get("skills")
        if isinstance(skills, list) and skills and isinstance(skills[0], dict):
            entry = str(skills[0].get("path") or skills[0].get("entry") or "").strip()
    if not _SAFE_ID.fullmatch(skill_id):
        raise SkillError("skill id 只能包含字母、数字、点、下划线、短横线")
    if not version:
        raise SkillError("技能包缺少 version")
    if not entry:
        raise SkillError("技能包缺少入口 SKILL.md（entry）")
    if entry.startswith("/") or ".." in PurePosixPath(entry).parts:
        raise SkillError("技能入口路径非法")
    contract = skill.get("contract") or manifest.get("contract") or {
        "schema": manifest.get("canvasSchema") or "drama-canvas-feed/v2"
    }
    schema = (
        str(contract.get("schema") or "")
        if isinstance(contract, dict)
        else ""
    )
    if schema != "drama-canvas-feed/v2":
        raise SkillError(
            f"技能包 contract.schema 必须是 drama-canvas-feed/v2，收到 {schema or '<missing>'}"
        )
    return {
        "id": skill_id,
        "name": name or skill_id,
        "version": version,
        "entry": entry,
        "source": source_name,
        "contract": contract,
        "raw": manifest,
    }


def list_skills() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM skills ORDER BY updated_at DESC").fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "activeVersion": row["active_version"],
                "rootPath": row["root_path"],
                "manifest": json.loads(row["manifest_json"]),
            }
            for row in rows
        ]


def get_skill(skill_id: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            return None
        version = conn.execute(
            "SELECT * FROM skill_versions WHERE skill_id=? AND version=?",
            (skill_id, row["active_version"]),
        ).fetchone()
        if not version:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "version": version["version"],
            "rootPath": version["root_path"],
            "manifest": json.loads(version["manifest_json"]),
        }


def read_entry(skill: dict[str, Any], max_chars: int) -> str:
    root = Path(skill["rootPath"])
    entry = str(skill["manifest"].get("entry") or "")
    path = (root / entry).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise SkillError("技能入口路径越界")
    if not path.is_file():
        raise SkillError(f"技能入口不存在：{entry}")
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def install_builtin_demo() -> None:
    if _BUILTIN.is_dir() and not list_skills():
        manifest_path, manifest, _ = _find_manifest(_BUILTIN)
        normalized = _normalize_manifest(manifest, "builtin")
        _save_version(normalized, _BUILTIN.resolve())


def upload_skill(filename: str, raw: bytes) -> dict[str, Any]:
    settings = get_settings()
    if len(raw) > settings.max_zip_bytes:
        raise SkillError(f"技能包超过 {settings.max_zip_bytes // (1024*1024)} MB")
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise SkillError("上传文件不是 zip")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-skill-") as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for item in _safe_members(zf, settings.max_zip_files):
                zf.extract(item, root)
        manifest_path, manifest, source_name = _find_manifest(root)
        normalized = _normalize_manifest(manifest, filename or source_name)
        root_dir = manifest_path.parent
        entry_path = (root_dir / normalized["entry"]).resolve()
        if not entry_path.is_file():
            raise SkillError(f"技能入口不存在：{normalized['entry']}")
        dest = settings.skills_dir / normalized["id"] / f"{normalized['version']}-{digest}"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root_dir, dest)
        _save_version(normalized, dest)
        return normalized


def _save_version(normalized: dict[str, Any], root_path: Path) -> None:
    manifest_json = json.dumps(normalized, ensure_ascii=False)
    now = utcnow()
    with db() as conn:
        existing = conn.execute("SELECT id FROM skills WHERE id=?", (normalized["id"],)).fetchone()
        conn.execute(
            """
            INSERT INTO skill_versions(skill_id, version, root_path, manifest_json, created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(skill_id, version) DO UPDATE SET
              root_path=excluded.root_path, manifest_json=excluded.manifest_json
            """,
            (normalized["id"], normalized["version"], str(root_path), manifest_json, now),
        )
        if existing:
            conn.execute(
                "UPDATE skills SET name=?, active_version=?, root_path=?, manifest_json=?, updated_at=? WHERE id=?",
                (normalized["name"], normalized["version"], str(root_path), manifest_json, now, normalized["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO skills(id, name, active_version, root_path, manifest_json, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (normalized["id"], normalized["name"], normalized["version"], str(root_path), manifest_json, now, now),
            )


def activate_version(skill_id: str, version: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM skill_versions WHERE skill_id=? AND version=?",
            (skill_id, version),
        ).fetchone()
        if not row:
            raise SkillError("该技能版本不存在")
        manifest = json.loads(row["manifest_json"])
        now = utcnow()
        conn.execute(
            "UPDATE skills SET active_version=?, root_path=?, manifest_json=?, updated_at=? WHERE id=?",
            (version, row["root_path"], row["manifest_json"], now, skill_id),
        )
    return {"id": skill_id, "version": version, "name": manifest.get("name") or skill_id}


def list_routes(skill_id: str) -> list[dict[str, Any]]:
    skill = get_skill(skill_id)
    if not skill:
        return []
    root = Path(skill["rootPath"]).resolve()
    routes: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        entry = child / "SKILL.md"
        if not child.is_dir() or not entry.is_file():
            continue
        title = ""
        try:
            for line in entry.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except OSError:
            pass
        routes.append({
            "id": child.name,
            "name": title or child.name,
            "entry": f"{child.name}/SKILL.md",
        })
    if not routes:
        routes.append({
            "id": "",
            "name": str(skill["manifest"].get("name") or skill_id),
            "entry": str(skill["manifest"].get("entry") or ""),
        })
    return routes


def read_route_entry(skill: dict[str, Any], route_id: str, max_chars: int) -> str:
    root = Path(skill["rootPath"]).resolve()
    if route_id:
        path = (root / route_id / "SKILL.md").resolve()
    else:
        path = (root / str(skill["manifest"].get("entry") or "")).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def list_versions(skill_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT version, root_path, manifest_json, created_at FROM skill_versions "
            "WHERE skill_id=? ORDER BY created_at DESC",
            (skill_id,),
        ).fetchall()
        active = conn.execute("SELECT active_version FROM skills WHERE id=?", (skill_id,)).fetchone()
    active_version = active["active_version"] if active else ""
    return [
        {
            "version": row["version"],
            "active": row["version"] == active_version,
            "createdAt": row["created_at"],
            "permissions": (json.loads(row["manifest_json"]).get("raw") or {}).get("permissions") or {},
        }
        for row in rows
    ]


DEFAULT_LOCKS = [
    {"key": "track", "label": "赛道", "options": ["国内剧", "海外剧", "解说", "漫剧"], "default": "国内剧"},
    {"key": "source", "label": "素材来源", "options": ["原创", "对标", "小说改编"], "default": "原创"},
    {"key": "modelVersion", "label": "模型版本", "options": ["Seedance 2.0", "Seedance 2.5"], "default": "Seedance 2.0"},
    {"key": "aspect", "label": "画幅", "options": ["9:16 竖屏", "16:9 横屏"], "default": "9:16 竖屏"},
    {"key": "delivery", "label": "交付形态", "options": ["15s 单段", "多段拼接"], "default": "15s 单段"},
    {"key": "seam", "label": "段缝", "options": ["无缝", "硬切", "转场"], "default": "无缝"},
]


def lock_schema(skill: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = skill.get("manifest") if isinstance(skill.get("manifest"), dict) else {}
    raw = manifest.get("raw") if isinstance(manifest.get("raw"), dict) else {}
    source = raw.get("locks") or manifest.get("locks")
    if isinstance(source, list) and source:
        result = []
        for index, item in enumerate(source):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            options = item.get("options")
            if not key or not isinstance(options, list) or not options:
                continue
            result.append({
                "key": key,
                "label": str(item.get("label") or key),
                "options": [str(option) for option in options],
                "default": str(item.get("default") or options[0]),
            })
        if result:
            return result
    return [dict(item) for item in DEFAULT_LOCKS]
