# Media Downloader Final v7

Aplikasi Streamlit untuk media publik YouTube, TikTok, dan Instagram yang dimiliki pengguna atau telah diizinkan untuk disimpan.

## Perbaikan utama v7

- Foto carousel selalu tersedia sebagai file JPG/PNG/WebP individual.
- ZIP semua foto bersifat opsional dan tidak menggantikan file individual.
- Setiap foto memiliki pratinjau dan tombol download sendiri.
- Foto Live JPG dan MOV dapat diunduh satu per satu; ZIP pasangan opsional.
- Pratinjau posting foto menampilkan gambar carousel.
- Pratinjau Foto Live menampilkan thumbnail dan mencoba memutar video sumber.
- Mode `Foto / Foto Live (Otomatis)` mendeteksi posting foto atau video.
- Bila Foto Live dipilih untuk posting foto statis, aplikasi otomatis memberikan foto individual alih-alih error.
- Pembuatan Foto Live hanya mengunduh potongan awal video yang diperlukan agar lebih cepat dan ringan.

## Deploy Streamlit Community Cloud

Upload seluruh isi folder ini ke root repository GitHub sehingga `app.py`, `requirements.txt`, dan `packages.txt` langsung terlihat di halaman utama repository.

Gunakan pengaturan:

- Branch: `main`
- Main file path: `app.py`

`packages.txt` memasang FFmpeg. `requirements.txt` memasang Streamlit, yt-dlp, gallery-dl, imageio-ffmpeg, dan Pillow.

## Penggunaan

1. Tempel URL publik.
2. Untuk kebutuhan foto/foto live, pilih `Foto / Foto Live (Otomatis)`.
3. Klik `Pratinjau`.
4. Pilih kualitas dan format.
5. Centang pernyataan izin.
6. Klik `Mulai Download`.
7. Download setiap file satu per satu, atau gunakan ZIP tambahan bila diaktifkan.

## Catatan

- "Versi asli" berarti kualitas tertinggi yang masih diberikan platform, bukan file master kamera.
- Aplikasi tidak membobol DRM, akun privat, paywall, atau pembatasan akses.
- Endpoint platform dapat berubah. Perbarui yt-dlp dan gallery-dl jika ekstraksi suatu saat gagal.
