ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS queue_state TEXT DEFAULT '';
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS available_at TEXT DEFAULT '';
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS worker_id TEXT DEFAULT '';
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0;
ALTER TABLE generation_tasks ADD COLUMN IF NOT EXISTS last_worker_error TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_generation_queue_claim
ON generation_tasks(status, available_at, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_generation_queue_lease
ON generation_tasks(lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_generation_queue_worker
ON generation_tasks(worker_id, heartbeat_at);
