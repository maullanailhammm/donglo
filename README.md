# Media Downloader Streamlit — Final v5

Aplikasi Streamlit untuk mengunduh media publik YouTube, TikTok, dan Instagram menggunakan `yt-dlp`, `gallery-dl`, dan FFmpeg. Gunakan hanya untuk konten milik sendiri, domain publik, berlisensi bebas, atau yang telah diizinkan pemiliknya.

## Fitur

- Video MP4/MKV dengan kualitas tertinggi sebagai default.
- Audio MP3 atau audio sumber tanpa konversi.
- Foto/carousel TikTok dan Instagram pada kualitas tertinggi yang disediakan platform.
- Thumbnail YouTube beresolusi tertinggi pada mode Foto.
- Foto Live dari URL video:
  - ZIP berisi JPG + MOV.
  - WebP animasi.
- Pilihan durasi Foto Live 3, 5, 10, atau 15 detik.
- ZIP otomatis untuk seluruh foto carousel.
- Pratinjau metadata dan perkiraan ukuran video.
- Ukuran file aktual setelah proses selesai.
- Tombol download browser pada deployment online.
- Deteksi FFmpeg sistem dan fallback portable dari `imageio-ffmpeg`.
- Konfigurasi siap Streamlit Community Cloud, Render Docker, Windows, dan GitHub Codespaces.

## Deploy Streamlit Community Cloud

1. Ekstrak ZIP.
2. Buka folder hasil ekstrak.
3. Upload **seluruh isi folder** ke root repository GitHub, bukan file ZIP-nya.
4. Pastikan file berikut berada langsung di root repository:

```text
app.py
downloader.py
requirements.txt
packages.txt
.streamlit/config.toml
```

5. Buat atau perbarui aplikasi di Streamlit Community Cloud dengan main file `app.py`.
6. Setelah `git push`, buka **Manage app → Reboot app**.

`packages.txt` memasang FFmpeg. `requirements.txt` memasang `yt-dlp`, `gallery-dl`, dan fallback FFmpeg portable.

## Lokal Windows

1. Jalankan `install.bat`.
2. Jalankan `run.bat`.

## Render

Buat Web Service dari repository dan gunakan runtime Docker. `Dockerfile` sudah memasang FFmpeg dan menjalankan Streamlit pada port platform.

## Codespaces

```bash
chmod +x setup_codespaces.sh
./setup_codespaces.sh
streamlit run app.py
```

## Catatan Foto dan Foto Live

- `gallery-dl` dipakai karena pengunduhan album foto bukan fokus utama `yt-dlp`.
- Postingan foto privat atau yang membutuhkan login tidak diproses oleh konfigurasi publik ini.
- Paket JPG + MOV merupakan bundle Foto Live portabel. Sebagian iPhone memerlukan aplikasi impor Live Photo agar pasangan tersebut dikenali sebagai satu item Live Photo native.
- WebP animasi lebih mudah diputar langsung, tetapi tidak sama dengan format Live Photo native Apple.
- Foto Live dibuat dari video sehingga membutuhkan encoding FFmpeg.
