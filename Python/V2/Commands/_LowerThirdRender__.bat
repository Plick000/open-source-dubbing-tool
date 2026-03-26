@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

REM ============================================================
REM _LowerThirdRender__.bat
REM 1. Runs AfterFX.com (Headless) -> _ReplaceTextAndRender.jsx
REM    (This updates the text and SAVES the .aep file)
REM 2. Runs aerender.exe -> Renders the saved .aep file
REM ============================================================

set "AE_DIR=C:\Program Files\Adobe\Adobe After Effects 2025\Support Files"
set "AE_COM=%AE_DIR%\AfterFX.com"
set "AE_RENDER=%AE_DIR%\aerender.exe"

set "JSX=%CD%\_ReplaceTextAndRender.jsx"
set "LOG=___LowerThird_Prep_Log___.txt"
set "DONE=___LowerThird_Prep_Done___.txt"

REM Cleanup previous run
if exist "%DONE%" del "%DONE%"
if exist "%LOG%"  del "%LOG%"

REM Kill existing AE instances to ensure clean slate
taskkill /F /IM "AfterFX.exe" /T >nul 2>&1
taskkill /F /IM "aerender.exe" /T >nul 2>&1

echo [LowerThirdRunner] Step 1: Updating Text and Saving Project...
"%AE_COM%" -noui -r "%JSX%"

if not exist "%DONE%" (
    echo [LowerThirdRunner] ERROR: JSX did not write %DONE%
    if exist "%LOG%" type "%LOG%"
    exit /b 1
)

REM Check for errors in the DONE file
findstr /i "ERR|" "%DONE%" >nul
if %errorlevel%==0 (
    echo [LowerThirdRunner] JSX reported error:
    type "%DONE%"
    if exist "%LOG%" type "%LOG%"
    exit /b 1
)

REM Parse the DONE file to get the AEP Path
REM Format expected: OK|READY_FOR_AERENDER|E:\Path\To\Project.aep
set "AEP_PATH="
for /f "tokens=3 delims=|" %%a in ('type "%DONE%"') do set "AEP_PATH=%%a"

if "%AEP_PATH%"=="" (
    echo [LowerThirdRunner] ERROR: Could not parse AEP Path from %DONE%
    type "%DONE%"
    exit /b 1
)

echo [LowerThirdRunner] Step 2: Starting AERENDER...
echo [LowerThirdRunner] Project: "%AEP_PATH%"

"%AE_RENDER%" -project "%AEP_PATH%"

if %errorlevel% NEQ 0 (
    echo [LowerThirdRunner] ❌ AERENDER Failed with exit code %errorlevel%
    exit /b %errorlevel%
)

echo [LowerThirdRunner] ✅ Render Complete.
popd
exit /b 0