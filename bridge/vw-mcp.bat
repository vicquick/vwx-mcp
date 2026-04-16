@echo off
REM VW MCP Server — bridges Claude Code (HTTP :8082) to Vectorworks plugin (socket :9878)
REM Start Vectorworks first, run the VW-MCP bridge script inside VW, then run this.

set DESKTOP_HOST=127.0.0.1
set VW_MCP_PORT=9878
set MCP_TRANSPORT=streamable-http
set FASTMCP_HOST=127.0.0.1
set FASTMCP_PORT=8082

python "%USERPROFILE%\.local\share\vw-mcp\vw_mcp_server.py"
pause
