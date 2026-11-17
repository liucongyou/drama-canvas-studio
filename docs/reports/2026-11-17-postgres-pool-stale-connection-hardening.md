# PostgreSQL Stale Connection Pool Hardening

Date: 2026-11-17
Code commit: `937f8c8`

## Problem

After PostgreSQL restarted, `/readyz` reported:

```text
terminating connection due to administrator command
```

Restarting the API, Worker, and Agent processes cleared the failure. The root
cause was the custom PostgreSQL pool in `pg_compat.py`: it discarded only
connections whose local `raw.closed` flag was true. psycopg can retain a
connection object with `closed == false` after PostgreSQL terminates the
backend, so the pool could hand a dead socket to one request after a database
restart or network interruption.

The existing SQLAlchemy `pool_pre_ping=True` is valid for SQLAlchemy call
paths, but it does not cover the production `pg_compat` connection pool.

## Change

- Ping pooled connections with `SELECT 1` before reuse.
- Roll back the probe transaction so the connection remains transaction-clean.
- Discard and close failed pooled connections.
- Replace stale connections without consuming an extra pool slot.
- Perform the probe outside the global pool lock so unrelated tenants do not
  serialize behind a database round-trip.
- Expose `staleTotal` in `databasePool` metrics for observability.
- Preserve the existing bounded pool size, wait timeout, schema idle limit, and
  pool exhaustion behavior.

## Verification

- `tests/test_pg_connection_pool.py`: 8 passed.
- Connection pool and multiprocess tests: 11 passed.
- Generation queue tests: 74 passed.
- PostgreSQL compatibility and fail-closed tests: 3 passed, 3 skipped because
  `TEST_POSTGRES_URL` is not configured locally.
- Python compilation passed.

The new regressions cover:

- a closed connection is replaced;
- a connection terminated by PostgreSQL is detected and replaced;
- four concurrent waiters recover from one stale connection without leaking
  active or waiting counters.

## Deployment

Deployment is limited to:

- `/www/wwwroot/yj.pduoduo.vip/app/pg_compat.py`
- `/www/wwwroot/yj.pduoduo.vip/app/RELEASE_COMMIT`

The release script backs up both files, restarts the API, Worker, and Agent,
requires a healthy database/migrations/worker ready check, and automatically
restores the backup if verification fails. Nginx and PostgreSQL are not
restarted.

No paid generation task is submitted during verification.

## Production Verification

- Deployed code commit:
  `937f8c81081d3f5fbe005cecb293acb9442242be`.
- Automatic rollback backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/`
  `pg-pool-ping-937f8c81081d3f5fbe005cecb293acb9442242be-20261117-224124`
- Deployed `pg_compat.py` SHA-256 matches the tested local file.
- API, Worker, and Agent are active.
- `/readyz` reports `ok=true`, database `ok=true`, migrations `ok=true`,
  worker fleet `ok=true`, two active workers, and zero active tasks.
- Live `databasePool` exposes `staleTotal` and reports zero waiting connections.
- Admin login through `https://yjapi.pduoduo.vip` succeeds.
- The public frontend returns HTTP 200 and serves the expected production entry.

An isolated production fault injection used a separate Python process, not the
live API pool:

1. A connection was returned to the test process pool.
2. PostgreSQL terminated that backend with `pg_terminate_backend`.
3. The next checkout detected the stale socket, discarded it, and created a
   replacement connection.
4. The replacement query returned `1`; `staleTotal` advanced to `1`; active and
   waiting counters returned to a clean state.

No paid image, audio, or video generation was submitted.
