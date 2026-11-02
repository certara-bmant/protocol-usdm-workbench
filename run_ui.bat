@echo off
REM ---------------------------------------------------------------------------
REM  protocol_to_usdm web UI launcher (Windows)
REM
REM  Creates a private virtual environment (outside OneDrive) with the NEWEST
REM  Python 3.10+ it can find, installs the dependencies, then starts the UI.
REM  If the newest Python cannot install the dependencies (e.g. a brand-new
REM  release without package wheels yet) it falls back to the next newest.
REM  Anaconda / whatever "python" is first on PATH is only a last resort.
REM
REM    run_ui.bat                 start (first run sets up the environment)
REM    run_ui.bat --reset         rebuild the environment, then start
REM    set PYTHON_EXE=C:\path\python.exe  &  run_ui.bat   force an interpreter
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

REM Anaconda can leave these set; they break other Pythons and venvs
set "PYTHONHOME="
set "PYTHONPATH="

set "BASE=%LOCALAPPDATA%\protocol-usdm-workbench"
set "VENV=%BASE%\venv"
set "VPY=%VENV%\Scripts\python.exe"
set "ARGS=%*"

if /i "%~1"=="--reset" (
    echo Removing old environment "%VENV%" ...
    if exist "%VENV%" rmdir /s /q "%VENV%"
    set "ARGS="
)

REM Reuse the environment if it exists and is healthy
if exist "%VPY%" (
    "%VPY%" -c "import sys, pdfplumber, openpyxl; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 goto run
    echo Existing environment is broken or too old - rebuilding it.
    rmdir /s /q "%VENV%"
)
if not exist "%BASE%" mkdir "%BASE%"

REM ---- try interpreters, newest first ------------------------------------------
if defined PYTHON_EXE (
    call :try "%PYTHON_EXE%"
    if exist "%VPY%" goto run
    echo PYTHON_EXE=%PYTHON_EXE% could not be used.
    goto fail
)

REM 1) Windows Python launcher (not affected by Anaconda on PATH)
for %%V in (3.16 3.15 3.14 3.13 3.12 3.11 3.10) do (
    if not exist "%VPY%" (
        py -%%V -c "import sys" >nul 2>&1 && call :try py -%%V
    )
)
if exist "%VPY%" goto run

REM 2) Standard python.org install locations
for %%V in (316 315 314 313 312 311 310) do (
    if not exist "%VPY%" (
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" call :try "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
    if not exist "%VPY%" (
        if exist "%ProgramFiles%\Python%%V\python.exe" call :try "%ProgramFiles%\Python%%V\python.exe"
    )
)
if exist "%VPY%" goto run

REM 3) Last resort: whatever "python" is on PATH (only if 3.10+)
call :try python
if exist "%VPY%" goto run

:fail
echo.
echo ERROR: could not set up Python 3.10 or newer with the required packages.
echo Install Python from https://www.python.org/downloads/ or point to one with:
echo     set PYTHON_EXE=C:\path\to\python.exe
echo     run_ui.bat
pause
exit /b 1

REM ---- subroutine: build the venv with interpreter %* ------------------------------
:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
echo.
echo Trying:
%* -c "import sys; print('  ', sys.executable, sys.version.split()[0])"
%* -m venv "%VENV%" >nul 2>&1
if errorlevel 1 (
    echo   could not create a virtual environment with this Python - skipping.
    if exist "%VENV%" rmdir /s /q "%VENV%"
    exit /b 1
)
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo   package install failed with this Python - trying the next one.
    rmdir /s /q "%VENV%"
    exit /b 1
)
echo   environment ready.
exit /b 0

REM ---- start ---------------------------------------------------------------------
:run
echo.
echo Starting protocol_to_usdm UI with:
"%VPY%" -c "import sys; print('  ', sys.executable, sys.version.split()[0])"
set "PYTHONDONTWRITEBYTECODE=1"
"%VPY%" -m protocol_to_usdm serve %ARGS%
if errorlevel 1 (
    echo.
    echo The UI stopped with an error - see the messages above.
    echo If it keeps failing, try:  run_ui.bat --reset
    pause
)
endlocal
