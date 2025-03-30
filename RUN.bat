@echo off
setlocal

REM --- Configuration ---
set VENV_DIR=.venv
set REQ_FILE=requirements.txt
set PYTHON_CMD=python

REM --- Check for Python in PATH ---
echo DEBUG: Line 10
echo Checking for Python installation...
echo DEBUG: Line 12 - Running Python version check
REM Run python --version and show output/errors
%PYTHON_CMD% --version
echo DEBUG: Line 13 - Checking Python error level
REM Check error level using preferred syntax
REM Check error level using || goto syntax
%PYTHON_CMD% --version || goto PythonCheckFailed
goto PythonCheckPassed

:PythonCheckFailed
echo ERROR: Python (%PYTHON_CMD%) command failed or Python was not found in your system's PATH.
echo Please install Python (3.8+ recommended) and ensure it's added to PATH.
goto EndScript

:PythonCheckPassed
REM Python check passed, continue script

REM --- Python check passed ---
echo DEBUG: Line 19 - Python check passed (after potential jump)
echo Python found.

REM --- Check/Setup Virtual Environment ---
echo DEBUG_CHECK_VENV_START
echo DEBUG: Line 34 - About to execute IF EXIST
IF EXIST ".venv\Scripts\activate.bat" goto VenvExists
goto VenvNotFound

:VenvExists
echo DEBUG: Line 24 - Venv exists path
REM --- Activate Existing Venv and Update ---
echo Activating existing virtual environment...
call "%VENV_DIR%\Scripts\activate.bat" || (
    echo ERROR: Failed to activate existing virtual environment.
    goto EndScript
)
echo Virtual environment activated.

echo Checking/updating requirements (forcing reinstall)...
REM Added --force-reinstall to ensure all packages are present and correct
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%REQ_FILE%" --no-cache-dir --disable-pip-version-check || (
    echo ERROR: Failed to update/reinstall requirements in existing venv. Check console output.
    echo Possible issues: network connection, file permissions, incompatible packages.
    echo For GPU features, ensure compatible drivers and potentially the Visual C++ Redistributable are installed.
    goto EndScript
)
echo Requirements are up to date.
goto LaunchApp

:VenvNotFound
REM --- Create New Venv and Install ---
echo Virtual environment not found. Creating...
%PYTHON_CMD% -m venv "%VENV_DIR%" || (
    echo ERROR: Failed to create virtual environment. Check Python installation and permissions.
    goto EndScript
)
echo Virtual environment created.

echo Activating new virtual environment...
call "%VENV_DIR%\Scripts\activate.bat" || (
    echo ERROR: Failed to activate newly created virtual environment.
    goto EndScript
)
echo Virtual environment activated.

echo Upgrading pip...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
if %ERRORLEVEL% neq 0 (
    echo WARNING: Failed to upgrade pip. Continuing with potentially older version.
)

echo Installing requirements from %REQ_FILE%...
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%REQ_FILE%" --no-cache-dir --disable-pip-version-check || (
    echo ERROR: Failed to install requirements. Check console output above.
    echo Possible issues: network connection, file permissions, incompatible packages.
    echo For GPU features, ensure compatible drivers and potentially the Visual C++ Redistributable are installed.
    goto EndScript
)
echo Requirements installed successfully.
goto LaunchApp

:LaunchApp
REM --- Code continues here after venv is ready ---

REM --- Launch Application ---
echo Starting Arc-Explorer...
REM Run the Python script using the venv's Python
"%VENV_DIR%\Scripts\python.exe" Arc-Explorer.py
echo Arc-Explorer finished.

REM --- Deactivate ---
REM Attempt to deactivate (might fail if venv wasn't active, ignore error)
IF EXIST "%VENV_DIR%\Scripts\deactivate.bat" (
    echo Deactivating virtual environment...
    call "%VENV_DIR%\Scripts\deactivate.bat"
)

:EndScript
echo.
pause
endlocal