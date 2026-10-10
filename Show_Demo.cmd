@echo off
setlocal
cd /d "%~dp0"
echo Starting ComfyShelf Showcase Demo...
start "" "%~dp0gui\demo.html"
exit /b 0
