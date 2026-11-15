CREATE TABLE IF NOT EXISTS generation_admissions(
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    lease_owner TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_admission_task
ON generation_admissions(task_id);

CREATE INDEX IF NOT EXISTS idx_generation_admission_scope
ON generation_admissions(tenant_id, provider, model, kind, expires_at);

CREATE INDEX IF NOT EXISTS idx_generation_admission_expiry
ON generation_admissions(expires_at);

CREATE TABLE IF NOT EXISTS generation_provider_cooldowns(
    scope_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    until_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generation_cooldown_until
ON generation_provider_cooldowns(until_at);
