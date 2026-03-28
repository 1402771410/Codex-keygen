@echo off
setlocal

set "_OLD_CP="
for /f "tokens=2 delims=: " %%a in ('chcp') do set "_OLD_CP=%%a"
chcp 65001 >nul

cd /d "%~dp0"

if "%~1"=="" (
    call .\keygen.bat
) else (
    call .\keygen.bat "%~1"
)

if defined _OLD_CP chcp %_OLD_CP% >nul
