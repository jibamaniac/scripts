@echo off
rem Sets up the ComfyUI-Kimodo nodes: finds Kimodo and writes kimodo_config.json.
rem Double-click this file after copying the ComfyUI-Kimodo folder into ComfyUI\custom_nodes.
rem Optional: pass the Kimodo folder, e.g.  setup_kimodo.bat E:\projects\kimodo
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0setup_kimodo.ps1" %*
echo.
pause
