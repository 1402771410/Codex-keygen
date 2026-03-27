@echo off
setlocal

set "_OLD_CP="
for /f "tokens=2 delims=: " %%a in ('chcp') do set "_OLD_CP=%%a"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d %~dp0

:: 传递 launcher 上下文供 help 输出使用
set KEYGEN_LAUNCHER=.\keygen.bat

if exist "%~dp0\.venv\Scripts\python.exe" (
    "%~dp0\.venv\Scripts\python.exe" "%~dp0\scripts\keygen.py" %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 "%~dp0\scripts\keygen.py" %*
    ) else (
        python "%~dp0\scripts\keygen.py" %*
    )
)

if defined _OLD_CP chcp %_OLD_CP% >nul
