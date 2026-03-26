@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"

REM ============================================================
REM run_title_render.bat (QUEUE TO AME)
REM - Runs AfterFX.com headless to execute prep_for_title.jsx
REM - JSX updates text/image, saves, queues to AME, writes done file
REM - Waits after queue to avoid AME race/fail
REM ============================================================
REM Your original behavior is preserved. :contentReference[oaicite:2]{index=2}

REM Allow override via environment (optional)
if "%AE_DIR%"=="" set "AE_DIR=C:\Program Files\Adobe\Adobe After Effects 2025\Support Files"

set "AFTERFX=%AE_DIR%\AfterFX.com"
set "JSX=%CD%\_TitleTextAndImageReplaceByDetections.jsx"
set "JOB=%CD%\_Title__job__.json"

if not exist "%AFTERFX%" (
  echo [RUN_BG] ERROR: AfterFX.com not found: "%AFTERFX%"
  exit /b 10
)

if not exist "%JSX%" (
  echo [RUN_BG] ERROR: JSX not found: "%JSX%"
  exit /b 11
)

if not exist "%JOB%" (
  echo [RUN_BG] ERROR: Job file not found: "%JOB%"
  exit /b 12
)

REM Clear old done file
if exist "%CD%\___Title_Prep_Done___.txt" del /q "%CD%\___Title_Prep_Done___.txt" >nul 2>&1

echo [RUN_BG] Starting AfterFX.com headless...
echo [RUN_BG] JSX: "%JSX%"

REM Run headless JSX
"%AFTERFX%" -noui -r "%JSX%"
set "RC=%ERRORLEVEL%"

REM Even if AfterFX returns 0, we still verify done file
if not exist "%CD%\___Title_Prep_Done___.txt" (
  echo [RUN_BG] ERROR: ___Title_Prep_Done___.txt not created. AfterFX exit=%RC%
  if exist "%CD%\___Title_Prep_Log___.txt" type "%CD%\___Title_Prep_Log___.txt"
  exit /b 21
)

findstr /b /c:"ERR|" "%CD%\___Title_Prep_Done___.txt" >nul
if %ERRORLEVEL%==0 (
  echo [RUN_BG] JSX returned ERROR:
  type "%CD%\___Title_Prep_Done___.txt"
  if exist "%CD%\___Title_Prep_Log___.txt" type "%CD%\___Title_Prep_Log___.txt"
  exit /b 22
)

echo [RUN_BG] OK:
type "%CD%\___Title_Prep_Done___.txt"

echo [RUN_BG] Waiting 8 seconds for AME stability...
timeout /t 8 /nobreak >nul

popd
exit /b 0
