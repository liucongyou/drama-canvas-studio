#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
AGENT_DIR="$APP/agent"
COMMIT=${1:?usage: $0 <release-commit>}
SKILL_ZIP=${2:?usage: $0 <release-commit> <skill-zip>}
RUNTIME_FILE=${3:?usage: $0 <release-commit> <skill-zip> <runtime.py>}
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/cinematic-grammar-agent-$COMMIT-$STAMP"
PREV_VERSION=""
SWITCHED=0

test -f "$SKILL_ZIP"
test -f "$RUNTIME_FILE"

mkdir -p "$BACKUP"
cp -a "$AGENT_DIR/app/runtime.py" "$BACKUP/runtime.py"
cp -a "$SKILL_ZIP" "$BACKUP/"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"
cp -a "$AGENT_DIR/data/skills/manju-laoli-skill" "$BACKUP/skills-manju-laoli" 2>/dev/null || true

set -a
. /etc/drama-canvas-agent.env
. /etc/drama-canvas-pg.env
set +a

cd "$AGENT_DIR"
PREV_VERSION=$(./.venv/bin/python - <<'PY'
from app.skills import get_skill
skill = get_skill("manju-laoli-skill")
print(skill.get("version") if skill else "")
PY
)

rollback() {
  set +e
  if [ "$SWITCHED" = "1" ]; then
    install -m 0644 "$BACKUP/runtime.py" "$AGENT_DIR/app/runtime.py"
    if [ -n "$PREV_VERSION" ]; then
      cd "$AGENT_DIR"
      ./.venv/bin/python - "$PREV_VERSION" <<'PY'
import sys
from app.skills import activate_version
version = sys.argv[1]
if version:
    activate_version("manju-laoli-skill", version)
PY
    fi
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
    chown www:www "$APP/RELEASE_COMMIT"
    systemctl restart drama-canvas-agent.service || true
  fi
  echo "rollback_completed backup=$BACKUP" >&2
}

finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    rollback
  fi
  exit "$status"
}
trap finish EXIT

install -m 0644 "$RUNTIME_FILE" "$AGENT_DIR/app/runtime.py"
SWITCHED=1

cd "$AGENT_DIR"
./.venv/bin/python - "$SKILL_ZIP" <<'PY'
import json
import sys
from pathlib import Path

from app.runtime import _system_prompt
from app.skills import activate_version, get_skill, list_versions, upload_skill

zip_path = Path(sys.argv[1])
uploaded = upload_skill(zip_path.name, zip_path.read_bytes())
assert uploaded["version"] == "7.4.35"
activated = activate_version("manju-laoli-skill", "7.4.35")
assert activated["version"] == "7.4.35"

skill = get_skill("manju-laoli-skill")
assert skill is not None
assert skill["version"] == "7.4.35"
prompt = _system_prompt(skill)
for marker in ("Agent 镜头语法合同", "信息还是反应", "Agent 输出前十二问", "画左、画右"):
    assert marker in prompt, marker

print(json.dumps({
    "activeVersion": skill["version"],
    "rootPath": skill["rootPath"],
    "promptLength": len(prompt),
    "versions": [
        {"version": item["version"], "active": item["active"]}
        for item in list_versions("manju-laoli-skill")
    ],
}, ensure_ascii=False))
PY

chown -R www:www "$AGENT_DIR/data/skills/manju-laoli-skill"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/RELEASE_COMMIT"
systemctl restart drama-canvas-agent.service

for _ in $(seq 1 30); do
  sleep 1
  if systemctl is-active --quiet drama-canvas-agent.service \
    && curl -fsS --max-time 5 http://127.0.0.1:8895/ >/dev/null 2>&1; then
    break
  fi
done

systemctl is-active --quiet drama-canvas-agent.service

cd "$AGENT_DIR"
./.venv/bin/python - <<'PY'
from app.runtime import _system_prompt
from app.skills import get_skill

skill = get_skill("manju-laoli-skill")
assert skill is not None
assert skill["version"] == "7.4.35"
prompt = _system_prompt(skill)
assert "Agent 镜头语法合同" in prompt
assert "Agent 输出前十二问" in prompt
print("post_restart_skill=", skill["version"])
print("post_restart_prompt_contract=present")
PY

trap - EXIT
echo "release=$COMMIT"
echo "skill_version=7.4.35"
echo "backup=$BACKUP"
