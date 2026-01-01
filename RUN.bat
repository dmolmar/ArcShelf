@echo off
setlocal

REM --- Configuration ---
set CONDA_ENV_NAME=arcshelf
set REQ_FILE=requirements.txt
set PYTHON_VERSION=3.10

REM --- Check for Conda installation ---
echo Checking for Conda installation...
where conda >nul 2>&1
if errorlevel 1 (
    echo ERROR: Conda not found in PATH.
    echo Please ensure Miniconda/Anaconda is installed and added to PATH.
    echo You may need to run this from Anaconda Prompt or add Conda to your system PATH.
    goto EndScript
)
echo Conda found.

REM --- Check if conda environment exists ---
echo Checking for '%CONDA_ENV_NAME%' environment...
call conda info --envs | findstr /C:"%CONDA_ENV_NAME%" >nul 2>&1
if errorlevel 1 goto CreateEnv
goto ActivateEnv

:CreateEnv
REM --- Create new conda environment with Python 3.10 ---
echo Environment '%CONDA_ENV_NAME%' not found. Creating with Python %PYTHON_VERSION%...
call conda create -n %CONDA_ENV_NAME% python=%PYTHON_VERSION% -y
if errorlevel 1 (
    echo ERROR: Failed to create conda environment.
    goto EndScript
)
echo Environment created successfully.

:ActivateEnv
REM --- Activate conda environment ---
echo Activating '%CONDA_ENV_NAME%' environment...
call conda activate %CONDA_ENV_NAME%
if errorlevel 1 (
    echo ERROR: Failed to activate conda environment.
    echo Try running this script from Anaconda Prompt.
    goto EndScript
)
echo Environment activated.

REM --- Install/Update requirements ---
echo Checking/updating requirements from %REQ_FILE%...
pip install -q -r "%REQ_FILE%" --no-cache-dir --disable-pip-version-check
if errorlevel 1 (
    echo ERROR: Failed to install requirements. Check console output above.
    echo Possible issues: network connection, file permissions, incompatible packages.
    echo For GPU features, ensure compatible drivers and potentially the Visual C++ Redistributable are installed.
    goto EndScript
)
echo Requirements are up to date.

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
echo model.onnx not found. Downloading...
where curl >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%MODEL_FILE%" "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx"
) else (
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/resolve/main/model.onnx' -OutFile '%MODEL_FILE%'"
)
if not exist "%MODEL_FILE%" (
    echo ERROR: Failed to download model.onnx.
    goto EndScript
)
goto ContinueAfterModelDownload

:ContinueAfterModelDownload

if not exist "%TAGS_FILE%" goto DownloadTags
goto ContinueAfterTagsDownload

:DownloadTags
echo.
echo [INFO] Downloading selected_tags.csv from HuggingFace...
echo selected_tags.csv not found. Downloading...
where curl >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    curl -L -o "%TAGS_FILE%" "https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/raw/main/selected_tags.csv"
) else (
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3/raw/main/selected_tags.csv' -OutFile '%TAGS_FILE%'"
)
if not exist "%TAGS_FILE%" (
    echo ERROR: Failed to download selected_tags.csv.
    goto EndScript
)
goto ContinueAfterTagsDownload

:ContinueAfterTagsDownload

REM --- Launch Application ---
echo Starting ArcShelf...
set ARCSHELF_LAUNCHED_VIA_BAT=1
REM Enable High-DPI scaling for Qt applications
set QT_ENABLE_HIGHDPI_SCALING=1
python main.py
echo ArcShelf finished.

:EndScript
pause
endlocal