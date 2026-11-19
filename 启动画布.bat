@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 戏剧画布 - 一键启动

rem ============================================================
rem   戏剧画布工作流系统 · 一键启动
rem
rem   双击运行，同时启动两个服务：
rem     前端 web-vimax (Vite)  ->  http://127.0.0.1:8891   <-- 浏览器打开这个
rem     后端 server.py         ->  http://127.0.0.1:8890   <-- 前端代理过去
rem
rem   可选参数：
rem     /auto      无交互（已在运行则跳过）
rem     /backend   只启动后端
rem     /front     只启动前端
rem
rem   依赖：Python 3.9+（后端纯标准库）、Node 18+（前端）
rem ============================================================

set "AUTO="
set "ONLY_BACK="
set "ONLY_FRONT="
for %%A in (%*) do (
    if /i "%%~A"=="/auto"    set "AUTO=1"
    if /i "%%~A"=="/backend" set "ONLY_BACK=1"
    if /i "%%~A"=="/front"   set "ONLY_FRONT=1"
)

cls
echo.
echo  ============================================================
echo    戏剧画布工作流系统  .  一键启动
echo  ============================================================
echo.
echo    前端    http://127.0.0.1:8891     (浏览器打开这个)
echo    后端    http://127.0.0.1:8890
echo.

rem ============================================================
rem  第 1 步：环境自检
rem ============================================================
echo  ------------------------------------------------------------
echo   [环境自检]
echo  ------------------------------------------------------------

rem ---- Python ----
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo   [X] 找不到 Python
    echo       请安装 Python 3.9+ 并勾选 "Add Python to PATH"
    echo       https://www.python.org/downloads/
    goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul
if errorlevel 1 (
    echo   [X] Python 版本过低，需要 3.9+
    %PY% --version
    goto :fail
)
for /f "delims=" %%V in ('%PY% --version 2^>^&1') do set "PYVER=%%V"
echo   [OK] !PYVER!

rem ---- Node（启用前端时必需）----
if not defined ONLY_BACK (
    where node >nul 2>nul
    if errorlevel 1 (
        echo   [X] 找不到 Node.js
        echo       前端开发服务需要 Node 18+ ：https://nodejs.org/
        goto :fail
    )
    for /f "delims=" %%V in ('node --version 2^>^&1') do set "NODEVER=%%V"
    echo   [OK] Node !NODEVER!

    if not exist "web-vimax\node_modules" (
        echo.
        echo   [!] 前端依赖未安装，正在执行 npm install ...
        echo       首次安装约 1-3 分钟，请耐心等待
        echo.
        pushd web-vimax
        call npm install
        set "NPM_RC=!errorlevel!"
        popd
        if not "!NPM_RC!"=="0" (
            echo   [X] npm install 失败，请手动进入 web-vimax 执行 npm install
            goto :fail
        )
        echo   [OK] 前端依赖安装完成
    ) else (
        echo   [OK] 前端依赖已就绪
    )
)

rem ---- 核心文件 ----
set "MISSING="
for %%F in (server.py canvas_store.py provider_client.py provider_catalog.py import_feed.py gate_check.py) do (
    if not exist "%%F" set "MISSING=!MISSING! %%F"
)
if defined MISSING (
    echo   [X] 缺少后端文件：!MISSING!
    echo       请确认本 bat 与 server.py 在同一目录
    goto :fail
)
echo   [OK] 后端文件完整
echo.

rem ============================================================
rem  第 2 步：端口检查（用 python 探测，最可靠）
rem ============================================================
echo  ------------------------------------------------------------
echo   [端口检查]
echo  ------------------------------------------------------------

set "P8890="
set "P8891="
if not defined ONLY_FRONT (
    for /f "usebackq delims=" %%P in (`%PY% -c "import socket;s=socket.socket();s.settimeout(1);print(s.connect_ex(('127.0.0.1',8890)));s.close()"`) do set "P8890=%%P"
)
if not defined ONLY_BACK (
    for /f "usebackq delims=" %%P in (`%PY% -c "import socket;s=socket.socket();s.settimeout(1);print(s.connect_ex(('127.0.0.1',8891)));s.close()"`) do set "P8891=%%P"
)

set "BACK_RUNNING="
set "FRONT_RUNNING="
if "!P8890!"=="0" set "BACK_RUNNING=1"
if "!P8891!"=="0" set "FRONT_RUNNING=1"

if not defined ONLY_FRONT (
    if defined BACK_RUNNING (
        echo   [..] 后端 8890  已在运行，跳过启动
    ) else (
        echo   [..] 后端 8890  未运行，待启动
    )
)
if not defined ONLY_BACK (
    if defined FRONT_RUNNING (
        echo   [..] 前端 8891  已在运行，跳过启动
    ) else (
        echo   [..] 前端 8891  未运行，待启动
    )
)

if defined BACK_RUNNING if defined FRONT_RUNNING (
    echo.
    echo   两个服务都已在运行，无需重复启动
    echo.
    echo   请直接打开： http://127.0.0.1:8891
    echo.
    if not defined AUTO (
        choice /c YN /n /m "  是否重新打开浏览器？[Y/N] "
        if !errorlevel!==1 start "" "http://127.0.0.1:8891/"
    )
    goto :ok
)
echo.

rem ============================================================
rem  第 3 步：启动后端（独立窗口）
rem ============================================================
if not defined ONLY_FRONT (
    if not defined BACK_RUNNING (
        echo  ------------------------------------------------------------
        echo   [启动] 后端  -^>  http://127.0.0.1:8890
        echo  ------------------------------------------------------------
        if not exist "data" mkdir "data" >nul 2>nul
        if not exist "logs" mkdir "logs" >nul 2>nul
        start "戏剧画布-后端(8890)" cmd /k "call "%~dp0scripts\load-local-agent-env.cmd"&& set PYTHONIOENCODING=gbk&& set PYTHONUTF8=0&& set CANVAS_MULTI_TENANT=1&& set CANVAS_ALLOW_SQLITE=1&& set CANVAS_AUTH_MODE=accounts&& set CANVAS_API_HOST=127.0.0.1&& set CANVAS_FRONTEND_ORIGIN=http://127.0.0.1:8891&& set CANVAS_ALLOWED_ORIGINS=http://127.0.0.1:8891&& set CANVAS_COOKIE_SECURE=0&& set CANVAS_OWNER_ADMIN_USERNAME=admin&& %PY% -u server.py"
        echo   已在新窗口启动
    )
)

rem ============================================================
rem  第 4 步：启动前端（独立窗口）
rem ============================================================
if not defined ONLY_BACK (
    if not defined FRONT_RUNNING (
        echo  ------------------------------------------------------------
        echo   [启动] 前端  -^>  http://127.0.0.1:8891
        echo  ------------------------------------------------------------
        start "戏剧画布-前端(8891)" cmd /k "cd /d "%~dp0web-vimax" && npx vite --port 8891 --host 127.0.0.1"
        echo   已在新窗口启动
    )
)
echo.

rem ============================================================
rem  第 5 步：等待就绪
rem ============================================================
echo  ------------------------------------------------------------
echo   [等待服务就绪]
echo  ------------------------------------------------------------
<nul set /p "="

set "CURL=%SystemRoot%\System32\curl.exe"
if defined BACK_RUNNING set "BACK_OK=1"
if defined FRONT_RUNNING set "FRONT_OK=1"
if defined ONLY_FRONT set "BACK_OK=1"
if defined ONLY_BACK set "FRONT_OK=1"

set /a TRY=0

:wait_loop
set /a TRY+=1
set "NEED_WAIT="

if not defined BACK_OK (
    "%CURL%" -s -o nul -m 2 --noproxy "*" http://127.0.0.1:8890/api/projects >nul 2>nul
    if not errorlevel 1 set "BACK_OK=1"
    if not defined BACK_OK set "NEED_WAIT=1"
)
if not defined FRONT_OK (
    "%CURL%" -s -o nul -m 2 --noproxy "*" http://127.0.0.1:8891/ >nul 2>nul
    if not errorlevel 1 set "FRONT_OK=1"
    if not defined FRONT_OK set "NEED_WAIT=1"
)

if defined NEED_WAIT if !TRY! LSS 45 (
    <nul set /p "=."
    ping -n 2 127.0.0.1 >nul
    goto :wait_loop
)
echo.

echo.
if not defined BACK_OK (
    echo   [X] 后端 8890 启动失败
    echo       请查看"戏剧画布-后端"窗口里的报错
    echo       常见原因：端口被防火墙拦 / data 目录无写权限
    goto :fail
)
if not defined FRONT_OK (
    echo   [X] 前端 8891 启动失败
    echo       请查看"戏剧画布-前端"窗口里的报错
    echo       常见原因：node_modules 损坏 / 8891 被占用
    goto :fail
)
if defined ONLY_FRONT set "FRONT_OK="
if defined ONLY_BACK (
    echo   [OK] 后端就绪
) else (
    echo   [OK] 前端 + 后端 均就绪
)

rem ============================================================
rem  第 6 步：打开浏览器
rem ============================================================
if not defined ONLY_BACK (
    if not defined ONLY_FRONT (
        echo.
        echo   正在打开浏览器 ...
        start "" "http://127.0.0.1:8891/"
    )
)

echo.
echo  ============================================================
echo    启动完成
echo  ============================================================
echo.
if defined ONLY_BACK (
    echo    后端接口   http://127.0.0.1:8890/api/projects
) else (
    echo    前端工作台  http://127.0.0.1:8891       ^<-- 用这个
    echo    后端接口    http://127.0.0.1:8890/api/projects
)
echo.
echo    数据库      data\canvas.db
echo.
echo    两个服务各在独立窗口运行
echo    关闭对应窗口 或 在窗口内按 Ctrl+C 即停止该服务
echo.
echo  ============================================================
echo.

if not defined AUTO pause
exit /b 0


:ok
if not defined AUTO pause
exit /b 0

:fail
echo.
echo  ============================================================
echo    启动中断，请按上面的提示处理后重试
echo  ============================================================
echo.
if not defined AUTO pause
exit /b 1
