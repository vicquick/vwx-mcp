@echo off
REM vwx-mcp Server — bridges Claude Code (HTTP :8082) to Vectorworks plugin (socket :9878)
REM Migrated to standalone fastmcp 3.x — runs in a self-bootstrapping venv.
REM 1. Start Vectorworks. 2. Run vwx-mcp bridge script inside VW. 3. Run this bat.

set DESKTOP_HOST=127.0.0.1
set VWX_MCP_PORT=9878
set MCP_TRANSPORT=streamable-http
set FASTMCP_HOST=127.0.0.1
set FASTMCP_PORT=8082
REM Optional toolset filter (cuts tool-overload tokens): full | gis | modeling | baumkataster | minimal
set VWX_TOOLSET=full

set VWXHOME=%USERPROFILE%\.local\share\vwx-mcp
set VENV=%VWXHOME%\.venv

REM --- one-time venv bootstrap (auto, idempotent) ---
if not exist "%VENV%\Scripts\python.exe" (
    echo [vwx-mcp] First run: creating venv + installing fastmcp ...
    python -m venv "%VENV%"
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install -r "%VWXHOME%\requirements.txt"
)

"%VENV%\Scripts\python.exe" "%VWXHOME%\vwx_mcp_server.py"
pause
