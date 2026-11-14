#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
AGENT_DIR="$APP/agent"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/skill-v7.4.33-agent-$STAMP"
SKILL_ZIP=/tmp/manju-laoli-skill-V7.4.33-20261114-r20.zip
RUNTIME_FILE=/tmp/drama-canvas-agent-runtime-v7.4.33.py

test -f "$SKILL_ZIP"
test -f "$RUNTIME_FILE"

mkdir -p "$BACKUP"
cp -a "$AGENT_DIR/app/runtime.py" "$BACKUP/runtime.py"
cp -a "$SKILL_ZIP" "$BACKUP/"

install -m 0644 "$RUNTIME_FILE" "$AGENT_DIR/app/runtime.py"

set -a
# Service configuration is the source of truth for PostgreSQL and Agent auth.
. /etc/drama-canvas-agent.env
. /etc/drama-canvas-pg.env
set +a

cd "$AGENT_DIR"
./.venv/bin/python - <<'PY'
from pathlib import Path

from app.runtime import _system_prompt
from app.skills import activate_version, get_skill, list_versions, upload_skill

skill_id = "manju-laoli-skill"
before = get_skill(skill_id)
print("active_before=", before.get("version") if before else None)

zip_path = Path("/tmp/manju-laoli-skill-V7.4.33-20261114-r20.zip")
uploaded = upload_skill(zip_path.name, zip_path.read_bytes())
print("uploaded=", uploaded["id"], uploaded["version"])

activated = activate_version(skill_id, "7.4.33")
print("activated=", activated)

skill = get_skill(skill_id)
assert skill is not None
assert skill["version"] == "7.4.33"
prompt = _system_prompt(skill)
assert "短视频钩子与平台语感合同" in prompt
assert "前 3 秒" in prompt or "观看理由" in prompt
print("active_after=", skill["version"])
print("prompt_contract=", "present")
print("versions=", [
    {"version": item["version"], "active": item["active"]}
    for item in list_versions(skill_id)
])
print("root_path=", skill["rootPath"])
PY

chown -R www:www "$AGENT_DIR/data/skills/manju-laoli-skill"

systemctl restart drama-canvas-agent.service
sleep 4
systemctl is-active drama-canvas-agent.service drama-canvas-yj.service
curl -fsS http://127.0.0.1:8895/ >/dev/null

./.venv/bin/python - <<'PY'
from app.runtime import _system_prompt
from app.skills import get_skill

skill = get_skill("manju-laoli-skill")
assert skill is not None
prompt = _system_prompt(skill)
assert skill["version"] == "7.4.33"
assert "短视频钩子与平台语感合同" in prompt
print("post_restart_skill=", skill["version"])
print("post_restart_prompt_contract=present")
PY

printf 'BACKUP=%s\n' "$BACKUP"
