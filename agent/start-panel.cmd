@echo off
cd /d "%~dp0"
call "%~dp0..\scripts\load-local-agent-env.cmd"
start "Canvas Agent" /min .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8895 --no-access-log
timeout /t 2 /nobreak >nul
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --app=http://127.0.0.1:8895/agent --window-size=480,1000 --window-position=1400,0
