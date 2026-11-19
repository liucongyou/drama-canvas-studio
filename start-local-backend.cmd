@echo off
setlocal
rem Stable local launcher. Keep startup flags here instead of composing a long
rem PowerShell/cmd command inline.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set CANVAS_MULTI_TENANT=1
set CANVAS_AUTH_MODE=accounts
set CANVAS_API_HOST=127.0.0.1
set CANVAS_FRONTEND_ORIGIN=http://127.0.0.1:8891
set CANVAS_ALLOWED_ORIGINS=http://127.0.0.1:8891
set CANVAS_COOKIE_SECURE=0
set CANVAS_OWNER_ADMIN_USERNAME=admin
rem Local launcher: use the existing SQLite store when PostgreSQL is not configured.
if not defined DATABASE_URL if not defined CANVAS_DATABASE_URL set CANVAS_ALLOW_SQLITE=1
call "%~dp0scripts\load-local-agent-env.cmd"
.venv\Scripts\python.exe -u server.py > _srv_out_new.log 2> _srv_err_new.log
