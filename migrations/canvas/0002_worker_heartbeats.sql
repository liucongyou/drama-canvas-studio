CREATE TABLE IF NOT EXISTS generation_worker_heartbeats(
    worker_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    active_tasks INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_generation_worker_heartbeat
ON generation_worker_heartbeats(heartbeat_at DESC);
