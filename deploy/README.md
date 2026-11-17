# Large Industrial Deployment

## Topology

Phase one uses three roles:

```text
reverse proxy
  -> drama-canvas-yj.service       API and static frontend
  -> drama-canvas-worker.service   durable generation queue workers
  -> drama-canvas-agent.service    Agent sidecar
          |
          v
      PostgreSQL 16
```

API and Worker are independent processes. They may run on one host initially,
but production supports placing them on different hosts without changing code
or HTTP contracts.

Production is PostgreSQL-only. SQLite is allowed only in isolated tests.

## Required Infrastructure

- PostgreSQL 16 with a dedicated application role.
- A reverse proxy preserving `Host`, `X-Forwarded-Proto` and response headers.
- A persistent `CANVAS_MASTER_KEY`.
- The same release commit on API and Worker hosts.
- Shared tenant media storage when API and Worker are on different hosts.
- Loopback-only scraping access to `/metrics`.

The API and Worker use the same `DATABASE_URL`, tenant registry, control schema
and release migration version.

## Environment Files

Production systemd services use:

```text
/etc/drama-canvas-agent.env
/etc/drama-canvas-pg.env
/etc/drama-canvas-worker-plane.env
```

`drama-canvas-worker-plane.env`:

```text
CANVAS_GENERATION_EXECUTION_MODE=worker
CANVAS_SCHEMA_MIGRATIONS_REQUIRED=1
CANVAS_PG_POOL_MAX=24
CANVAS_PG_POOL_IDLE_MAX=8
CANVAS_PG_POOL_WAIT_TIMEOUT_MS=10000
CANVAS_GENERATION_GLOBAL_CONCURRENCY=30
CANVAS_GENERATION_TENANT_CONCURRENCY=30
CANVAS_GENERATION_PROVIDER_CONCURRENCY=20
CANVAS_BUDGET_GUARD_TIMEOUT_SECONDS=10
CANVAS_BUDGET_GUARD_MAX_WAITERS=4
CANVAS_MEDIA_DELIVERY_MODE=auto
CANVAS_WORKER_CONCURRENCY=4
CANVAS_WORKER_POLL_SECONDS=0.5
CANVAS_WORKER_LEASE_SECONDS=90
CANVAS_WORKER_ADMISSION_TTL_SECONDS=300
CANVAS_WORKER_DRAIN_SECONDS=30
CANVAS_QUEUE_AGE_PROMOTION_SECONDS=300
```

The sum of API, Worker, migration and administrative PostgreSQL connections must
remain below `max_connections`, with emergency headroom reserved.

Generation budget checks use tenant-scoped PostgreSQL advisory locks so multiple
API processes cannot reserve the same remaining budget concurrently. Guard
connections come from a separate bounded direct-connection budget so waiting
submitters cannot exhaust the main application pool.

`CANVAS_MEDIA_DELIVERY_MODE` controls the media durability contract:

- `auto`: local bytes or object storage are both valid.
- `local`: intended only when API and Worker share the same upload volume.
- `object`: requires enabled and verified object storage before media
  generation submission; readiness fails closed otherwise.

## Schema Migrations

Apply migrations before switching service code:

```bash
cd /www/wwwroot/yj.pduoduo.vip/app
set -a
. /etc/drama-canvas-worker-plane.env
. /etc/drama-canvas-pg.env
set +a
.venv/bin/python scripts/run-schema-migrations.py \
  --url "$DATABASE_URL" \
  --apply \
  --report backups/schema-migrations.json
```

Verify:

```bash
.venv/bin/python scripts/run-schema-migrations.py \
  --url "$DATABASE_URL" \
  --check
```

Production API and Worker readiness fail closed when migrations are missing,
unknown or checksum-mismatched.

## Services

```bash
systemctl restart drama-canvas-worker.service
systemctl restart drama-canvas-yj.service
systemctl restart drama-canvas-agent.service
```

Required state:

```bash
systemctl is-active drama-canvas-yj.service
systemctl is-active drama-canvas-worker.service
systemctl is-active drama-canvas-agent.service
```

The Worker service must run as `www`, use the same app release, and have write
access only to the tenant data path and uploads.

## Shared Media

Worker result materialization may write local `/uploads` files before a terminal
node commit. For split-host deployment, use one of:

- a shared persistent volume mounted at the tenant data root on API and Worker;
- durable object storage with local staging;
- an authenticated media service reachable from every API instance.

A URL that exists only on one Worker host is invalid.

## Readiness

Loopback endpoints:

```text
http://127.0.0.1:8890/healthz
http://127.0.0.1:8890/readyz
http://127.0.0.1:8890/metrics
```

`/healthz` is process liveness.

`/readyz` checks:

- database connectivity;
- required schema migrations;
- an active Worker heartbeat when API execution mode is `worker`.

`/metrics` is Prometheus text format and must not be exposed publicly by Nginx.

## Alert Thresholds

Recommended initial alerts:

| Condition | Warning | Critical |
|---|---:|---:|
| No active Worker heartbeat | 30 s | 90 s |
| Oldest queued task | 10 min | 30 min |
| PostgreSQL pool waiters | >0 for 30 s | >0 for 2 min |
| PostgreSQL connections | 70% of max | 85% of max |
| Expired task leases | >5 | >20 |
| Provider uncertainty rate | >2% | >5% |
| Disk free | <15% | <8% |
| Audit/front-end error growth | 2x weekly baseline | 4x weekly baseline |

## Rolling Restart

API restart:

1. Remove the instance from the reverse-proxy pool.
2. Wait for in-flight HTTP drain.
3. Stop the API service.
4. Start the new release.
5. Wait for `/readyz`.
6. Return the instance to the proxy pool.

Worker restart:

1. Stop claiming new tasks.
2. Continue active tasks for the drain window.
3. Leave unfinished leases recoverable.
4. Start the new Worker.
5. Wait for a current release heartbeat.

## Tenant Lifecycle

Use the server-side administration command:

```bash
.venv/bin/python tenant_admin.py list
.venv/bin/python tenant_admin.py create alpha --name "Alpha Studio"
.venv/bin/python tenant_admin.py account-create alpha alice
.venv/bin/python tenant_admin.py invite alpha --ttl-hours 24
```

Additional commands:

```bash
.venv/bin/python tenant_admin.py account-password <account-id>
.venv/bin/python tenant_admin.py account-disable <account-id>
.venv/bin/python tenant_admin.py account-enable <account-id>
.venv/bin/python tenant_admin.py disable alpha
.venv/bin/python tenant_admin.py enable alpha
```

## Reverse Proxy

The public entrypoint serves the frontend and proxies API calls. The application
binds only to loopback. Nginx/Caddy must:

- preserve the original Host;
- provide HTTPS and the security headers in `nginx-yj-security-headers.conf`;
- reject public access to `/metrics`;
- allow SSE responses without proxy buffering;
- forward Range headers and preserve `206` responses.

## Rollback

Keep:

- previous API runtime files;
- previous Worker runtime files;
- previous frontend dist;
- previous worker-plane environment;
- database snapshot taken before migrations;
- migration reports and release commit.

If readiness or Worker recovery fails:

1. stop new Worker and API services;
2. restore the previous runtime and environment;
3. restore the database only when a migration rollback is required;
4. restart the previous API and Worker;
5. verify health, queue state and task ownership.
