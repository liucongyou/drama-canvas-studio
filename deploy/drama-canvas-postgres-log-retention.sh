#!/usr/bin/env bash
set -euo pipefail

LOG_DIR=/www/server/pgsql/logs
RETENTION_DAYS=3
MAX_BYTES=1073741824

[[ -d "$LOG_DIR" ]] || exit 0

find "$LOG_DIR" -maxdepth 1 -type f \
  -name 'postgresql-*.log' \
  -mtime "+$RETENTION_DAYS" \
  -delete

total_bytes() {
  find "$LOG_DIR" -maxdepth 1 -type f -name 'postgresql-*.log' \
    -printf '%s\n' \
    | awk '{sum += $1} END {printf "%.0f\n", sum + 0}'
}

logger_pid=$(
  pgrep -u postgres -f 'postgres: logger' \
    | head -n 1 \
    || true
)
open_log=''
if [[ -n "$logger_pid" ]]; then
  while IFS= read -r fd_path; do
    target=$(readlink -f "$fd_path" 2>/dev/null || true)
    if [[ "$target" == "$LOG_DIR"/postgresql-*.log ]]; then
      open_log="$target"
      break
    fi
  done < <(find "/proc/$logger_pid/fd" -maxdepth 1 -type l 2>/dev/null)
fi

total=$(total_bytes)
if (( total <= MAX_BYTES )); then
  exit 0
fi

while IFS=$'\t' read -r _mtime size path; do
  [[ -n "$path" ]] || continue
  [[ "$path" == "$open_log" ]] && continue
  rm -f -- "$path"
  total=$((total - size))
  (( total <= MAX_BYTES )) && break
done < <(
  find "$LOG_DIR" -maxdepth 1 -type f -name 'postgresql-*.log' \
    -printf '%T@\t%s\t%p\n' \
    | sort -n
)
