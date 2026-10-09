@echo off
setlocal EnableExtensions DisableDelayedExpansion
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-bootstrap.ps1"
set "COMFYSHELF_SETUP_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %COMFYSHELF_SETUP_RESULT%
