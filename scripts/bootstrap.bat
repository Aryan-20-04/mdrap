@echo off
setlocal
cd /d "%~dp0\.."
python scripts\bootstrap.py %*
if %ERRORLEVEL% neq 0 (
    echo [BOOTSTRAP FAILED]
    exit /b %ERRORLEVEL%
)
