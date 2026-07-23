@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment belum tersedia.
    echo Jalankan install.bat terlebih dahulu.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m streamlit run app.py

if errorlevel 1 (
    echo.
    echo Aplikasi berhenti karena error.
    pause
)
