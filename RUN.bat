@echo off
setlocal

REM --- Configuration ---
set VENV_DIR=.venv
set REQ_FILE=requirements.txt
set PYTHON_CMD=python

REM --- Check for Python in PATH ---
echo Checking for Python installation...
REM Run python --version and show output/errors
REM Check error level using preferred syntax
REM Check error level using || goto syntax
%PYTHON_CMD% --version
if errorlevel 1 goto PythonCheckFailed
goto PythonCheckPassed

:PythonCheckFailed
echo ERROR: Python (%PYTHON_CMD%) command failed or Python was not found in your system's PATH.
echo Please install Python (3.8+ recommended) and ensure it's added to PATH.
goto EndScript

:PythonCheckPassed
REM Python check passed, continue script

REM --- Python check passed ---
echo Python found.

REM --- Check/Setup Virtual Environment ---
IF EXIST ".venv\Scripts\activate.bat" goto VenvExists
goto VenvNotFound

:VenvExists
REM --- Activate Existing Venv and Update ---
echo Activating existing virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: Failed to activate existing virtual environment.
    goto EndScript
)
echo Virtual environment activated.

echo Checking/updating requirements...
REM Added --force-reinstall to ensure all packages are present and correct
"%VENV_DIR%\Scripts\python.exe" -m pip install -q -r "%REQ_FILE%" --no-cache-dir --disable-pip-version-check
if errorlevel 1 (
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
%PYTHON_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment. Check Python installation and permissions.
    goto EndScript
)
echo Virtual environment created.

echo Activating new virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
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
"%VENV_DIR%\Scripts\python.exe" -m pip install -q -r "%REQ_FILE%" --no-cache-dir --disable-pip-version-check
if errorlevel 1 (
    echo ERROR: Failed to install requirements. Check console output above.
    echo Possible issues: network connection, file permissions, incompatible packages.
    echo For GPU features, ensure compatible drivers and potentially the Visual C++ Redistributable are installed.
    goto EndScript
)
echo Requirements installed successfully.
goto LaunchApp

:LaunchApp
REM --- Code continues here after venv is ready ---

REM --- Hardware Detection and ONNX Installation ---
echo Detecting hardware for ONNX installation... | powershell -Command "Tee-Object -FilePath setup.log -Append"
set ONNX_PKG=onnxruntime==1.15.1
nvidia-smi >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    powershell -Command "Write-Output 'NVIDIA GPU detected. Will install onnxruntime-gpu 1.15.1.' | Tee-Object -FilePath setup.log -Append"
    set ONNX_PKG=onnxruntime-gpu==1.15.1
) else (
    powershell -Command "Write-Output 'No NVIDIA GPU detected. Will install onnxruntime (CPU) 1.15.1.' | Tee-Object -FilePath setup.log -Append"
)

echo Installing ONNX package: %ONNX_PKG% | powershell -Command "Tee-Object -FilePath setup.log -Append"
"%VENV_DIR%\Scripts\python.exe" -m pip install -q %ONNX_PKG% --no-cache-dir --disable-pip-version-check
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to install %ONNX_PKG%. See setup.log for details. | powershell -Command "Tee-Object -FilePath setup.log -Append"
    goto EndScript
)

REM --- Check for model.onnx and selected_tags.csv ---
set MODELS_DIR=models
set MODEL_FILE=%MODELS_DIR%\model.onnx
set TAGS_FILE=%MODELS_DIR%\selected_tags.csv

if not exist "%MODELS_DIR%" mkdir "%MODELS_DIR%"

if not exist "%MODEL_FILE%" goto DownloadModel
goto ContinueAfterModelDownload

:DownloadModel
echo.
echo [INFO] Downloading model.onnx from HuggingFace (over 1GB, this may take a while)...
echo model.onnx not found. Downloading... | powershell -Command "Tee-Object -FilePath setup.log -Append"
where curl >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%MODEL_FILE%" "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx"
) else (
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx' -OutFile '%MODEL_FILE%'"
)
if not exist "%MODEL_FILE%" (
    echo ERROR: Failed to download model.onnx. | powershell -Command "Tee-Object -FilePath setup.log -Append"
    goto EndScript
)
goto ContinueAfterModelDownload

:ContinueAfterModelDownload

if not exist "%TAGS_FILE%" goto DownloadTags
goto ContinueAfterTagsDownload

:DownloadTags
echo.
echo [INFO] Downloading selected_tags.csv from HuggingFace...
echo selected_tags.csv not found. Downloading... | powershell -Command "Tee-Object -FilePath setup.log -Append"
where curl >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%TAGS_FILE%" "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/raw/main/selected_tags.csv"
) else (
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/raw/main/selected_tags.csv' -OutFile '%TAGS_FILE%'"
)
if not exist "%TAGS_FILE%" (
    echo ERROR: Failed to download selected_tags.csv. | powershell -Command "Tee-Object -FilePath setup.log -Append"
    goto EndScript
)
goto ContinueAfterTagsDownload

:ContinueAfterTagsDownload

REM --- Launch Application ---
echo Starting ArcShelf... | powershell -Command "Tee-Object -FilePath setup.log -Append"
set ARCSHELF_LAUNCHED_VIA_BAT=1
REM Enable High-DPI scaling for Qt applications
set QT_ENABLE_HIGHDPI_SCALING=1
"%VENV_DIR%\Scripts\python.exe" main.py
echo ArcShelf finished. | powershell -Command "Tee-Object -FilePath setup.log -Append"

REM --- Deactivate ---
REM Attempt to deactivate (might fail if venv wasn't active, ignore error)
IF EXIST "%VENV_DIR%\Scripts\deactivate.bat" (
    echo Deactivating virtual environment... | powershell -Command "Tee-Object -FilePath setup.log -Append"
    call "%VENV_DIR%\Scripts\deactivate.bat"
)

:EndScript
powershell -Command "Add-Content -Path setup.log -Value ''"
REM pause
endlocal