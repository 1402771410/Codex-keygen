@echo off
setlocal
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
