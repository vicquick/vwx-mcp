@echo off
title VWX Bridge Watchdog
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vwx_watchdog.ps1"
pause
