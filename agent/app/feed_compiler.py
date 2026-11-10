from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.feed_normalizer import heal_voice_prompts, normalize_feed


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_feed_contract  # noqa: E402


@dataclass(frozen=True)
class CompiledFeed:
    feed: dict[str, Any]
    errors: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    schema_sha256: str
    feed_sha256: str
    auto_healed: bool

    @property
    def ok(self) -> bool:
        return not self.errors


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feed_sha256(feed: dict[str, Any]) -> str:
    canonical = copy.deepcopy(feed)
    contract = canonical.get("canvas_contract")
    generator = contract.get("generator") if isinstance(contract, dict) else None
    if isinstance(generator, dict):
        generator.pop("feedSha256", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_feed_draft(
    draft: dict[str, Any],
    *,
    skill_id: str,
    skill_version: str,
    package_revision: str = "",
) -> CompiledFeed:
    """Compile a model draft into the canonical canvas Feed contract.

    The model owns story content. This function owns mechanical identity:
    schema version, skill provenance, figure codes, voice shape and hashes.
    It never deletes segments, roles or dialogue to hide a validation error.
    """
    source = draft if isinstance(draft, dict) else {}
    feed = normalize_feed(
        copy.deepcopy(source),
        skill_id=skill_id,
        skill_version=skill_version,
        package_revision=package_revision,
    )
    healed = heal_voice_prompts(feed)
    auto_healed = healed is not feed
    if healed is not feed:
        feed = normalize_feed(
            healed,
            skill_id=skill_id,
            skill_version=skill_version,
            package_revision=package_revision,
        )

    contract = feed.setdefault("canvas_contract", {})
    if not isinstance(contract, dict):
        contract = {}
        feed["canvas_contract"] = contract
    contract["schema"] = canvas_feed_contract.SCHEMA_VERSION
    generator = contract.setdefault("generator", {})
    if not isinstance(generator, dict):
        generator = {}
        contract["generator"] = generator
    schema_path = ROOT / "schemas" / "canvas_feed.schema.json"
    schema_sha256 = _sha256_file(schema_path)
    generator.update(
        {
            "name": str(skill_id or "canvas-agent"),
            "skillVersion": str(skill_version or "0.0.0"),
            "packageRevision": str(package_revision or skill_version or "agent"),
            "contractModule": "canvas_feed_contract.py",
            "schemaSha256": schema_sha256,
        }
    )

    gate = canvas_feed_contract.analyze_declared_feed(feed)
    errors = tuple(
        canvas_feed_contract.issue_dict(item)
        for item in gate.errors
    )
    warnings = tuple(
        canvas_feed_contract.issue_dict(item)
        for item in gate.warnings
    )
    feed_sha256 = _feed_sha256(feed)
    generator["feedSha256"] = feed_sha256
    return CompiledFeed(
        feed=feed,
        errors=errors,
        warnings=warnings,
        schema_sha256=schema_sha256,
        feed_sha256=feed_sha256,
        auto_healed=auto_healed,
    )
