"""Create the Drama Canvas relational shadow schema.

Revision ID: 20261110_0001
Revises:
Create Date: 2026-11-10
"""
from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "20261110_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "schema"
        / "drama_canvas_postgres.sql"
    )
    op.execute(schema.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_shares CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_events CASCADE")
    op.execute("DROP TABLE IF EXISTS generation_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_projection_notes CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_projection_episodes CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_projection_edges CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_projection_nodes CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_project_meta CASCADE")
    op.execute("DROP TABLE IF EXISTS canvas_projects CASCADE")
