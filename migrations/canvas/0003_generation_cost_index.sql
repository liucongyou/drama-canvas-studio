CREATE INDEX IF NOT EXISTS idx_generation_tasks_created_at
ON generation_tasks(created_at DESC);
