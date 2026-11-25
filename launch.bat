@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AutoClip - AI Gameplay Video Clipper

if not exist ".venv\Scripts\autoclip.exe" (
  echo [error] The app is not installed yet.
  echo         Right-click install.ps1 ^> "Run with PowerShell", or run:
  echo         powershell -ExecutionPolicy Bypass -File install.ps1
  echo.
  pause
  exit /b 1
)

rem Optional settings from .env (KEY=VALUE lines; lines starting with # are ignored).
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
    if not "%%b"=="" set "%%a=%%b"
  )
)
if "%AUTOCLIP_PORT%"=="" set "AUTOCLIP_PORT=8765"

set "URL=http://localhost:%AUTOCLIP_PORT%"

rem Avoid duplicate instances: if something already answers on this port, just open it.
set "HEALTH=000"
for /f %%s in ('curl.exe -s -o NUL -m 3 -w "%%{http_code}" "http://127.0.0.1:%AUTOCLIP_PORT%/api/health" 2^>NUL') do set "HEALTH=%%s"
if "%HEALTH%"=="200" (
  echo The app is already running at %URL%
  start "" "%URL%"
  timeout /t 3 >NUL
  exit /b 0
)

echo.
echo  AutoClip - AI Gameplay Video Clipper
echo  ------------------------------------------------------------
echo  The backend and web interface run together in this window.
echo  Opening %URL%
echo  Keep this window open while you work. Close it (or press Ctrl+C) to stop.
echo.

".venv\Scripts\autoclip.exe" serve --host 127.0.0.1 --port %AUTOCLIP_PORT%
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
  echo.
  echo [error] The app stopped with exit code %CODE%.
  echo         Common causes: port %AUTOCLIP_PORT% in use ^(change AUTOCLIP_PORT in .env^),
  echo         or a broken install ^(run: powershell -File install.ps1 -Recreate^).
  echo         See TROUBLESHOOTING.md.
  echo.
  pause
)
endlocal
exit /b %CODE%
