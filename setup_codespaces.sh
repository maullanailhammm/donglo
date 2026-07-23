#!/usr/bin/env bash
set -euo pipefail

echo "Memasang FFmpeg dan dependency Python..."
sudo apt-get update
sudo apt-get install -y ffmpeg ca-certificates
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Verifikasi:"
command -v ffmpeg
command -v ffprobe
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
echo
echo "Selesai. Jalankan: streamlit run app.py"
