@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "COMFYSHELF_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if defined COMFY_PYTHON set "COMFYSHELF_PYTHON=%COMFY_PYTHON%"

if not exist "%COMFYSHELF_PYTHON%" (
  echo ComfyShelf could not find its Python environment.
  echo.
  echo First double-click Setup_ComfyShelf.cmd in this folder.
  echo Bitte zuerst Setup_ComfyShelf.cmd in diesem Ordner doppelklicken.
  echo.
  echo If COMFY_PYTHON is set, check that override too.
  echo Help / Hilfe: docs\guide.en.md / docs\guide.de.md
  pause
  exit /b 2
)

start "" "%COMFYSHELF_PYTHON%" "%~dp0workflow_switcher_gui.py"
if errorlevel 1 (
  echo ComfyShelf could not start. See README.md for troubleshooting.
  pause
  exit /b 2
)
exit /b 0
