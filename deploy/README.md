# Multi-tenant deployment

The public entrypoint is the Python service in `server.py`. It owns tenant
routing, account/session validation, project data, provider configuration,
uploads and generation queue state. The Node/ViMax helper service is not a
public gateway; it stays on loopback or is omitted from the deployment.

## Required infrastructure

- A base domain, for example `example.com`.
- Wildcard DNS such as `*.example.com` -> the reverse proxy.
- A wildcard TLS certificate, or a reverse proxy that obtains certificates for
  the concrete tenant hosts.
- A reverse proxy that preserves the original `Host` header.
- A persistent master key for `CANVAS_MASTER_KEY`. Losing it makes every
  encrypted provider and TOS credential unreadable.

The recommended account-mode topology is one frontend host and one API host:

```text
yj.example.com       -> static frontend, login page and canvas
yjapi.example.com    -> session/auth and all API endpoints
```

Set `CANVAS_AUTH_MODE=accounts`, `CANVAS_API_HOST=yjapi.example.com`,
`CANVAS_FRONTEND_ORIGIN=https://yj.example.com`, and
`CANVAS_ALLOWED_ORIGINS=https://yj.example.com`. The API returns CORS headers
only for the configured frontend origin.

## Process setup

Install Python 3.11+ and install `requirements.txt`. Set the variables from
`tenant.env.example`, then start:

```powershell
py -m pip install -r requirements.txt
py server.py
```

`server.py` binds to `127.0.0.1:8890`. Do not expose that port directly. Put
Caddy, Nginx, or another TLS reverse proxy in front of it. The main domain must
remain a `404`; only tenant subdomains should reach the application.

## Tenant lifecycle

Run the local administration command on the server:

```powershell
py tenant_admin.py create alpha --name "Alpha Studio"
py tenant_admin.py account-create alpha alice
py tenant_admin.py invite alpha --ttl-hours 24
```

The account command prompts for a password. If the password is lost:

```powershell
py tenant_admin.py account-password acc_xxx
py tenant_admin.py account-disable acc_xxx
py tenant_admin.py account-enable acc_xxx
```

The invite URL is an alternative one-time onboarding path. It is printed once
and is single-use. If it expires or is lost:

```powershell
py tenant_admin.py rotate-invite alpha --ttl-hours 24
```

Other commands:

```powershell
py tenant_admin.py list
py tenant_admin.py disable alpha
py tenant_admin.py enable alpha
py tenant_admin.py cleanup
```

## First start and legacy migration

When `CANVAS_MULTI_TENANT=1`, the first start creates the owner tenant and
copies the existing legacy data into:

```text
<CANVAS_TENANT_DATA_ROOT>/tenants/owner/canvas.db
<CANVAS_TENANT_DATA_ROOT>/tenants/owner/uploads/
<CANVAS_TENANT_DATA_ROOT>/tenants/owner/config.local.json
```

The legacy files are left in place as a recovery copy. New tenants receive an
empty project database and their own empty provider/TOS configuration.

## Isolation guarantees

- Tenant identity comes from the subdomain plus a server-side session cookie.
- In account mode, tenant identity comes only from the server-side account
  session; the browser never supplies `tenant_id`.
- The main domain and invalid hosts return `404`.
- Each tenant has a separate SQLite database, upload directory, config file,
  logs directory and generation queue counters.
- Provider and TOS keys are encrypted at rest and API responses only expose
  masked hints.
- A TOS bucket or access key already bound to one tenant cannot be claimed by
  another tenant.

The reverse proxy, DNS records, TLS certificate and master-key backup are
infrastructure responsibilities and are not created by the application.

