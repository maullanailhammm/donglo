@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo Instalasi Media Downloader
echo ==============================================

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher tidak ditemukan.
    echo Instal Python 3.10 atau lebih baru, lalu jalankan file ini lagi.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Gagal membuat virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Instalasi selesai.
echo Aplikasi menyertakan FFmpeg portable sebagai cadangan.
echo Jalankan run.bat untuk membuka aplikasi.
pause
exit /b 0

:error
echo.
echo Instalasi gagal. Periksa koneksi internet dan pesan error di atas.
pause
exit /b 1
