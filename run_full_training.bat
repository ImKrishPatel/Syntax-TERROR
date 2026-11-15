@echo off
rem Trains the MODIS -> VIIRS models on every yearly FIRMS archive.
rem Double-click this file, or run it from a terminal. It can be started
rem again after an interruption and carries on from where it stopped.
setlocal
cd /d "%~dp0"

rem Folder that holds the downloaded bundle ZIPs (nasa05-16.zip and so on).
set "BUNDLES=%USERPROFILE%\Downloads\NASA"
set "STATUS=full_training_status.txt"

echo [%date% %time%] started >> "%STATUS%"

if not exist ".venv\Scripts\python.exe" (
    echo Creating a private Python environment in .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" in the installer^) and run this file again.
    echo [%date% %time%] FAILED: python not found >> "%STATUS%"
    pause
    exit /b 1
)
set "PY=.venv\Scripts\python.exe"

rem An interrupted pip upgrade leaves "~ip" folders behind; clear them.
for /d %%D in (".venv\Lib\site-packages\~*") do rmdir /s /q "%%D"

echo.
echo === Installing the Python packages ===
rem --only-binary makes pip use ready-made packages and never try to
rem compile one, which needs Visual Studio on Windows.
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet --only-binary=:all: "numpy>=1.26" "pandas>=2.2" scipy "scikit-learn>=1.5" joblib pyproj rasterio torch
if errorlevel 1 goto failed
echo [%date% %time%] packages installed >> "%STATUS%"

echo.
echo === Copying the yearly archives into data\raw ===
if exist "%BUNDLES%" (
    "%PY%" -m spatial_ml.data.unpack_bundles --bundles "%BUNDLES%" --raw-dir data\raw
    if errorlevel 1 goto failed
) else (
    echo %BUNDLES% not found, using what is already in data\raw
)
echo [%date% %time%] archives ready >> "%STATUS%"

echo.
echo === Cleaning the archives and matching MODIS with VIIRS ===
echo This is the long part. Progress for each year is written to
echo data\overlap\real_full\logs
"%PY%" -W ignore -m spatial_ml.pipeline.full_run --archives data\raw --processed-dir data\processed\firms_real_full --output-dir data\overlap\real_full
if errorlevel 1 goto failed
echo [%date% %time%] matching finished >> "%STATUS%"

echo.
echo === Training the Random Forest and the CNN ensemble ===
findstr /c:"\"status\": \"complete\"" "results\real_m7_full\real_training_manifest.json" >nul 2>nul
if not errorlevel 1 (
    echo Training already finished earlier. Delete results\real_m7_full and
    echo models\real_full if you want to train again.
    goto done
)
rem A half-finished earlier attempt is moved aside, not deleted.
if exist "results\real_m7_full" ren "results\real_m7_full" "real_m7_full_old_%RANDOM%"
if exist "models\real_full" ren "models\real_full" "real_full_old_%RANDOM%"
"%PY%" -W ignore -m spatial_ml.evaluation.train_real --sample-dir data\overlap\real_full --results-dir results\real_m7_full --models-dir models\real_full --train-end 2021 --validation-end 2023
if errorlevel 1 goto failed

:done
echo [%date% %time%] ALL DONE >> "%STATUS%"
echo.
echo All done. Models are in models\real_full and the reports in results\real_m7_full
pause
exit /b 0

:failed
echo [%date% %time%] FAILED >> "%STATUS%"
echo.
echo Something went wrong. Scroll up to see the message, then run this file again.
pause
exit /b 1
