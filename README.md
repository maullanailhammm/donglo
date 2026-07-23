# Media Downloader Streamlit — Final

Aplikasi Streamlit untuk memproses media publik YouTube, TikTok, dan Instagram menggunakan `yt-dlp` dan FFmpeg. Gunakan hanya untuk konten milik sendiri, domain publik, berlisensi bebas, atau yang telah diizinkan pemiliknya.

## Fitur

- Video MP4/MKV dan pilihan batas resolusi.
- Audio MP3 atau audio sumber tanpa konversi.
- Pratinjau metadata dan perkiraan ukuran per resolusi.
- Ukuran file aktual setelah proses selesai.
- Tombol download browser pada deployment online.
- Deteksi FFmpeg sistem dan fallback portable dari `imageio-ffmpeg`.
- Konfigurasi siap Streamlit Community Cloud, Render Docker, Windows, dan GitHub Codespaces.

## Deploy Streamlit Community Cloud

1. Ekstrak ZIP.
2. Upload **seluruh isi folder** ke root repository GitHub.
3. Pastikan `app.py`, `requirements.txt`, dan `packages.txt` berada di root.
4. Buat aplikasi di Streamlit Community Cloud dengan main file `app.py`.
5. Setelah update, lakukan **Reboot app**.

`packages.txt` memasang FFmpeg sistem. Jika instalasi sistem tidak terdeteksi, aplikasi memakai binary portable dari `imageio-ffmpeg`.

## Lokal Windows

1. Jalankan `install.bat`.
2. Jalankan `run.bat`.

## Render

Buat Web Service dari repository dan pilih runtime Docker. `Dockerfile` sudah memasang FFmpeg dan menjalankan Streamlit pada port platform.

## Codespaces

Jalankan:

```bash
chmod +x setup_codespaces.sh
./setup_codespaces.sh
streamlit run app.py
```
