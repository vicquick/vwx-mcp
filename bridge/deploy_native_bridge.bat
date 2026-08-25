@echo off
REM Deploy the VWX Bridge native plugin (palette). Vectorworks must be CLOSED
REM (the .vlb is DLL-locked while it runs). Self-elevates for the Program Files
REM copy. VW keeps ~5 windowless child processes alive for a few seconds after
REM the window closes — this script WAITS for them instead of aborting.

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

REM Build output sits next to this script in the repo — deriving it keeps the
REM script working from any clone (and keeps a developer's home directory out
REM of a public repository).
set "SRC=%~dp0..\native\Output\Release"

REM Vectorworks major version was hardcoded as 2026 here, in the deploy target
REM AND in the process name below. The process-name copy is the dangerous one:
REM on any other version the "wait for VW to close" guard silently never
REM matches, reports success, and the copy then fails on a DLL still locked by
REM a running Vectorworks. Discover the newest installed version instead;
REM override with VWX_VW_VERSION.
set "VWVER=%VWX_VW_VERSION%"
if not defined VWVER (
  for /f "delims=" %%D in ('dir /b /ad /o-n "C:\Program Files\Vectorworks *" 2^>nul') do (
    if not defined VWVER for /f "tokens=2" %%V in ("%%D") do set "VWVER=%%V"
  )
)
if not defined VWVER (
  echo   No "C:\Program Files\Vectorworks ^<year^>" install found.
  echo   Set VWX_VW_VERSION=2026 ^(or your version^) and rerun.
  pause
  exit /b 1
)
set "DST=C:\Program Files\Vectorworks %VWVER%\Plug-ins"
set "VWEXE=Vectorworks%VWVER%.exe"
echo Target: Vectorworks %VWVER%

echo Waiting for Vectorworks to exit completely (up to 60 s)...
set /a tries=0
:waitloop
tasklist /FI "IMAGENAME eq %VWEXE%" | find /I "%VWEXE%" >nul
if %errorlevel% neq 0 goto :vwclosed
set /a tries+=1
if %tries% geq 30 (
  echo.
  echo   Vectorworks is STILL RUNNING after 60 s. Close it completely
  echo   ^(check Task Manager for lingering %VWEXE%^) and rerun.
  echo.
  pause
  exit /b 1
)
timeout /t 2 /nobreak >nul
goto :waitloop

:vwclosed
echo Vectorworks is closed. Deploying...
echo.
echo Source build:
for %%F in ("%SRC%\VwxBridge.vlb") do echo   %%~tF  %%~zF bytes  %%F

copy /Y "%SRC%\VwxBridge.vlb" "%DST%\" || goto :fail
copy /Y "%SRC%\VwxBridge.vwr" "%DST%\" || goto :fail

REM Vectorworks credentials file, if one has been issued. It must sit beside the
REM plug-in it covers, and without it VW shows the "Unknown Developer Plug-ins"
REM dialog at every launch. Optional: absent until Vectorworks returns the .vst
REM for the request in native\CredentialsVwxMcp.json — see docs\PLUGIN_CREDENTIALS.md.
if exist "%~dp0..\native\Credentials*.vst" (
  copy /Y "%~dp0..\native\Credentials*.vst" "%DST%\" >nul && echo   credentials file deployed
) else (
  echo   no credentials file ^(expect the "Unbekannte Entwickler-Plug-ins" dialog^)
)

REM verify: deployed size must equal source size
for %%F in ("%SRC%\VwxBridge.vlb") do set "SRCSIZE=%%~zF"
for %%F in ("%DST%\VwxBridge.vlb") do set "DSTSIZE=%%~zF"
if not "%SRCSIZE%"=="%DSTSIZE%" (
  echo.
  echo   VERIFY FAILED: deployed size %DSTSIZE% ^!= source size %SRCSIZE%.
  echo.
  pause
  exit /b 1
)

echo.
echo   DEPLOYED + VERIFIED (%DSTSIZE% bytes).
echo   Reopen Vectorworks, then show the VWX Bridge palette
echo   (Extras menu -^> "VWX Bridge Palette anzeigen"). The palette self-pumps:
echo   no watchdog, no hotkey, no focus needed.
echo.
pause
exit /b 0

:fail
echo.
echo   COPY FAILED (error %errorlevel%). Is Vectorworks really closed?
echo   (The .vlb stays DLL-locked until every %VWEXE% is gone.)
echo.
pause
exit /b 1
