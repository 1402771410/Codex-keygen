@echo off
setlocal
cd /d %~dp0

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
