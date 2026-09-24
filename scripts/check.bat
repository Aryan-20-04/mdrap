@echo off
python scripts\check.py %*
if %ERRORLEVEL% NEQ 0 (
    exit /b %ERRORLEVEL%
)
