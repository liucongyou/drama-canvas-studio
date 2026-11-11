#!/usr/bin/env bash
set -euo pipefail

PGLOG=/www/server/pgsql/logs/postgresql-2026-11-11.log
ARCHIVE=/root/yj-pg-log-archive-20261111

mkdir -p "$ARCHIVE"
test -f "$PGLOG"
chmod 600 "$PGLOG"

# Copy the active log to the root filesystem, then truncate the same inode.
# Copying preserves the useful diagnostic history; truncation is safe for the
# currently open PostgreSQL log handle.
cp --reflink=auto "$PGLOG" "$ARCHIVE/postgresql-2026-11-11.full.log"
: > "$PGLOG"

sleep 1
df -h /www
ls -lh "$PGLOG" "$ARCHIVE/postgresql-2026-11-11.tail.log"
