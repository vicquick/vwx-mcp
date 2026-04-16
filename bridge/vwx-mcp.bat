@echo off
REM vwx-mcp Server — bridges Claude Code (HTTP :8082) to Vectorworks plugin (socket :9878)
REM 1. Start Vectorworks. 2. Run vwx-mcp bridge script inside VW. 3. Run this bat.

set DESKTOP_HOST=127.0.0.1
set VWX_MCP_PORT=9878
set MCP_TRANSPORT=streamable-http
set FASTMCP_HOST=127.0.0.1
set FASTMCP_PORT=8082

python "%USERPROFILE%\.local\share\vwx-mcp\vwx_mcp_server.py"
pause
