#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
AGENT_DIR="$APP/agent"
MAIN_SERVICE=drama-canvas-yj.service
AGENT_SERVICE=drama-canvas-agent.service
WORKER_SERVICE=drama-canvas-worker.service
COMMIT=${1:?usage: $0 <release-commit> <bundle.tar.gz>}
BUNDLE=${2:?usage: $0 <release-commit> <bundle.tar.gz>}
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE="/tmp/aicg3d-recipe-$COMMIT-$STAMP"
BACKUP="$APP/backups/aicg3d-recipe-$COMMIT-$STAMP"
PREV_SKILL_VERSION=""
SWITCHED=0

test -f "$BUNDLE"
mkdir -p "$STAGE" "$BACKUP"
tar -tzf "$BUNDLE" | grep -Eq '(^/|(^|/)\.\.(/|$))' && {
  echo "unsafe bundle paths" >&2
  exit 1
}
tar -xzf "$BUNDLE" -C "$STAGE"
test -f "$STAGE/server.py"
test -f "$STAGE/recipe_contract.py"
test -f "$STAGE/agent/app/runtime.py"
test -f "$STAGE/agent/app/resources/aicg3d_recipes/catalog.v1.json"
test -f "$STAGE/web-vimax/dist/index.html"
test -f "$STAGE/deliverables/manju-laoli-skill-V7.4.36-20261119-r24.zip"

cp -a "$APP/server.py" "$BACKUP/server.py"
cp -a "$APP/canvas_store.py" "$BACKUP/canvas_store.py"
cp -a "$APP/import_feed.py" "$BACKUP/import_feed.py"
cp -a "$APP/recipe_contract.py" "$BACKUP/recipe_contract.py" 2>/dev/null || true
cp -a "$APP/schemas" "$BACKUP/schemas"
cp -a "$APP/agent/app" "$BACKUP/agent-app"
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT" 2>/dev/null || true
cp -a "$AGENT_DIR/data/skills/manju-laoli-skill" "$BACKUP/skills-manju-laoli" 2>/dev/null || true

set -a
. /etc/drama-canvas-agent.env
. /etc/drama-canvas-pg.env
set +a

cd "$AGENT_DIR"
PREV_SKILL_VERSION=$(./.venv/bin/python - <<'PY'
from app.skills import get_skill
skill = get_skill("manju-laoli-skill")
print(skill.get("version") if skill else "")
PY
)

rollback() {
  set +e
  echo "rollback_started backup=$BACKUP" >&2
  systemctl stop "$WORKER_SERVICE" "$AGENT_SERVICE" "$MAIN_SERVICE" 2>/dev/null || true
  install -m 0644 "$BACKUP/server.py" "$APP/server.py"
  install -m 0644 "$BACKUP/canvas_store.py" "$APP/canvas_store.py"
  install -m 0644 "$BACKUP/import_feed.py" "$APP/import_feed.py"
  if [ -f "$BACKUP/recipe_contract.py" ]; then
    install -m 0644 "$BACKUP/recipe_contract.py" "$APP/recipe_contract.py"
  else
    rm -f "$APP/recipe_contract.py"
  fi
  rm -rf "$APP/schemas"
  cp -a "$BACKUP/schemas" "$APP/schemas"
  rm -rf "$APP/agent/app.rollback"
  mv "$APP/agent/app" "$APP/agent/app.rollback"
  cp -a "$BACKUP/agent-app" "$APP/agent/app"
  rm -rf "$APP/web-vimax/dist.rollback"
  mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.rollback"
  cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  if [ -n "$PREV_SKILL_VERSION" ]; then
    cd "$AGENT_DIR"
    ./.venv/bin/python - "$PREV_SKILL_VERSION" <<'PY'
import sys
from app.skills import activate_version
version = sys.argv[1]
if version:
    activate_version("manju-laoli-skill", version)
PY
  fi
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  chown -R www:www "$APP/server.py" "$APP/canvas_store.py" "$APP/import_feed.py" \
    "$APP/schemas" "$APP/agent/app" "$APP/web-vimax/dist"
  if [ -f "$APP/recipe_contract.py" ]; then
    chown www:www "$APP/recipe_contract.py"
  fi
  systemctl start "$MAIN_SERVICE" || true
  systemctl start "$AGENT_SERVICE" || true
  systemctl start "$WORKER_SERVICE" 2>/dev/null || true
  echo "rollback_completed" >&2
}

finish() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$SWITCHED" = "1" ]; then
    rollback
  fi
  exit "$status"
}
trap finish EXIT

systemctl stop "$WORKER_SERVICE" 2>/dev/null || true
systemctl stop "$AGENT_SERVICE" "$MAIN_SERVICE"

install -m 0644 "$STAGE/server.py" "$APP/server.py"
install -m 0644 "$STAGE/canvas_store.py" "$APP/canvas_store.py"
install -m 0644 "$STAGE/import_feed.py" "$APP/import_feed.py"
install -m 0644 "$STAGE/recipe_contract.py" "$APP/recipe_contract.py"
rm -rf "$APP/schemas.next"
cp -a "$STAGE/schemas" "$APP/schemas.next"
rm -rf "$APP/schemas.previous"
mv "$APP/schemas" "$APP/schemas.previous"
mv "$APP/schemas.next" "$APP/schemas"

rm -rf "$APP/agent/app.next"
cp -a "$STAGE/agent/app" "$APP/agent/app.next"
rm -rf "$APP/agent/app.previous"
mv "$APP/agent/app" "$APP/agent/app.previous"
mv "$APP/agent/app.next" "$APP/agent/app"

rm -rf "$APP/web-vimax/dist.next"
cp -a "$STAGE/web-vimax/dist" "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"

chown -R www:www "$APP/server.py" "$APP/canvas_store.py" "$APP/import_feed.py" \
  "$APP/recipe_contract.py" "$APP/schemas" "$APP/agent/app" "$APP/web-vimax/dist"
SWITCHED=1

cd "$AGENT_DIR"
./.venv/bin/python - \
  "$STAGE/deliverables/manju-laoli-skill-V7.4.36-20261119-r24.zip" <<'PY'
import json
import sys
from pathlib import Path

from app.runtime import _system_prompt
from app.skills import activate_version, get_skill, upload_skill

zip_path = Path(sys.argv[1])
uploaded = upload_skill(zip_path.name, zip_path.read_bytes())
assert uploaded["version"] == "7.4.36"
activate_version("manju-laoli-skill", "7.4.36")
skill = get_skill("manju-laoli-skill")
assert skill and skill["version"] == "7.4.36"
prompt = _system_prompt(skill)
for marker in (
    "Agent AICG3D 镜头配方合同",
    "每个段落最多一个 primary",
    "正文 @ 才是出镜和素材引用的唯一权威",
):
    assert marker in prompt, marker
print(json.dumps({
    "activeVersion": skill["version"],
    "rootPath": skill["rootPath"],
    "promptLength": len(prompt),
}, ensure_ascii=False))
PY
chown -R www:www "$AGENT_DIR/data/skills/manju-laoli-skill"

printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/RELEASE_COMMIT"

systemctl start "$MAIN_SERVICE"
systemctl start "$AGENT_SERVICE"
if systemctl list-unit-files | grep -q '^drama-canvas-worker.service'; then
  systemctl start "$WORKER_SERVICE"
fi

for _ in $(seq 1 60); do
  sleep 1
  main_ok=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8890/readyz || true)
  agent_ok=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8895/ || true)
  if [ "$main_ok" = "200" ] && [ "$agent_ok" = "200" ]; then
    break
  fi
done

systemctl is-active --quiet "$MAIN_SERVICE"
systemctl is-active --quiet "$AGENT_SERVICE"
curl -fsS --max-time 10 http://127.0.0.1:8890/readyz >/dev/null

cd "$AGENT_DIR"
./.venv/bin/python - <<'PY'
from app import recipes
from app.skills import get_skill

skill = get_skill("manju-laoli-skill")
assert skill and skill["version"] == "7.4.36"
info = recipes.metadata()
assert info["recipeCount"] == 295
assert info["kindCounts"] == {"creative": 292, "tool": 3}
search = recipes.search(query="群演聚焦接力", limit=3)
assert search["recipes"][0]["id"] == "handoff-ensemble-focus"
print(f"post_restart_skill={skill['version']}")
print(f"post_restart_recipes={info['recipeCount']}")
print(f"post_restart_search={search['recipes'][0]['id']}")
PY

trap - EXIT
rm -rf "$APP/agent/app.previous" "$APP/web-vimax/dist.previous" "$APP/schemas.previous"
echo "release=$COMMIT"
echo "skill_version=7.4.36"
echo "backup=$BACKUP"
