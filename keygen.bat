@echo off
setlocal

set "_OLD_CP="
for /f "tokens=2 delims=: " %%a in ('chcp') do set "_OLD_CP=%%a"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0"
if errorlevel 1 goto cd_failed

set "KEYGEN_EXIT_CODE=1"
set "KEYGEN_HAS_ARGS=0"
set "KEYGEN_DID_PACKAGE=0"
set "PY_CMD="
set "PY_ARGS="
if not "%~1"=="" set "KEYGEN_HAS_ARGS=1"

if exist "%~dp0\.venv\Scripts\python.exe" (
    "%~dp0\.venv\Scripts\python.exe" -V >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=%~dp0\.venv\Scripts\python.exe"
        set "PY_ARGS="
        goto run
    )
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -V >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=py"
        set "PY_ARGS=-3"
        goto run
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -V >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=python"
        set "PY_ARGS="
        goto run
    )
)

goto python_missing

:run
if "%~1"=="" goto run_windows

if /i "%~1"=="-h" goto show_help
if /i "%~1"=="--help" goto show_help

echo [INFO] Output directory: %~1
call "%PY_CMD%" %PY_ARGS% "%~dp0\scripts\package_manager.py" --target windows --output-dir "%~1"
set "KEYGEN_EXIT_CODE=%ERRORLEVEL%"
set "KEYGEN_DID_PACKAGE=1"
goto finish

:run_windows
call "%PY_CMD%" %PY_ARGS% "%~dp0\scripts\package_manager.py" --target windows
set "KEYGEN_EXIT_CODE=%ERRORLEVEL%"
set "KEYGEN_DID_PACKAGE=1"
goto finish

:show_help
echo Usage:
echo   keygen.bat
echo   keygen.bat [OUTPUT_DIR]
echo.
echo Examples:
echo   keygen.bat
echo   keygen.bat D:\build-output
echo.
echo Note: Windows host always packages windows target only.
set "KEYGEN_EXIT_CODE=0"
goto finish

:python_missing
>&2 echo [ERROR] No usable Python 3 interpreter found.
>&2 echo Checked: "%~dp0\.venv\Scripts\python.exe", py -3, python
set "KEYGEN_EXIT_CODE=9009"
goto finish

:cd_failed
>&2 echo [ERROR] Cannot enter project directory: %~dp0
set "KEYGEN_EXIT_CODE=1"
goto finish

:finish
if not defined KEYGEN_EXIT_CODE set "KEYGEN_EXIT_CODE=1"
if "%KEYGEN_DID_PACKAGE%"=="1" if not "%KEYGEN_NO_PAUSE%"=="1" (
    echo.
    if "%KEYGEN_EXIT_CODE%"=="0" (
        echo [OK] Packaging completed.
        echo [INFO] Release directory and executable path are shown above.
    ) else (
        echo.
        echo [ERROR] keygen.bat exit code: %KEYGEN_EXIT_CODE%
    )
    echo.
    echo Packaging flow finished. Window will stay open.
    echo Press any key to close this window...
    pause >nul
)

if not "%KEYGEN_DID_PACKAGE%"=="1" (
    if not "%KEYGEN_EXIT_CODE%"=="0" (
        if "%KEYGEN_HAS_ARGS%"=="0" if not "%KEYGEN_NO_PAUSE%"=="1" (
            echo.
            echo [ERROR] keygen.bat exit code: %KEYGEN_EXIT_CODE%
            echo Press any key to close this window...
            pause >nul
        )
    )
)
if defined _OLD_CP chcp %_OLD_CP% >nul
exit /b %KEYGEN_EXIT_CODE%
