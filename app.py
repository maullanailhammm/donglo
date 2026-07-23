from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import streamlit as st
import yt_dlp

from downloader import (
    DownloadSettings,
    default_download_directory,
    detect_ffmpeg,
    detect_gallery_dl,
    download_media,
    human_bytes,
    parse_urls,
    preview_media,
    selected_format_summary,
    summarize_files,
    validate_public_url,
)


st.set_page_config(page_title="Media Downloader", page_icon="⬇️", layout="wide")

CLOUD_MODE = os.getenv("MEDIA_DOWNLOADER_CLOUD", "").lower() in {"1", "true", "yes"} or os.name != "nt"
CLOUD_URL_LIMIT = 3
LOCAL_URL_LIMIT = 20
CLOUD_MAX_FILE_BYTES = 300 * 1024 * 1024
SESSION_ROOT = Path(tempfile.gettempdir()) / "media_downloader_sessions"


def cleanup_old_sessions(max_age_hours: int = 12) -> None:
    if not SESSION_ROOT.exists():
        return
    cutoff = time.time() - (max_age_hours * 3600)
    for child in SESSION_ROOT.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def get_session_download_dir() -> Path:
    if "download_session_id" not in st.session_state:
        st.session_state.download_session_id = uuid.uuid4().hex
    path = SESSION_ROOT / st.session_state.download_session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_session_files() -> None:
    path = get_session_download_dir()
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    st.session_state.last_results = []


def render_preview(data: dict[str, Any]) -> None:
    left, right = st.columns([1, 2])
    with left:
        if data.get("thumbnail"):
            st.image(data["thumbnail"], use_container_width=True)
        else:
            st.info("Pratinjau gambar tidak tersedia, tetapi URL tetap dapat dicoba.")
    with right:
        st.subheader(data.get("title", "Tanpa judul"))
        st.write(f"**Jenis media:** {data.get('media_type', '-')}")
        st.write(f"**Pengunggah:** {data.get('uploader', '-')}")
        st.write(f"**Durasi:** {data.get('duration', '-')}")
        st.write(f"**Platform:** {data.get('extractor', '-')}")
        heights = data.get("available_heights") or []
        resolution_text = ", ".join(f"{height}p" for height in heights[:10]) or "Tidak tersedia"
        st.write(f"**Resolusi video tersedia:** {resolution_text}")
        st.write(f"**Perkiraan kualitas tertinggi:** {data.get('estimated_best_size', 'Tidak tersedia')}")

    size_estimates = data.get("size_estimates") or []
    if size_estimates:
        with st.expander("Perkiraan ukuran per resolusi", expanded=False):
            st.dataframe(size_estimates, use_container_width=True, hide_index=True)
            st.caption("Nilai merupakan perkiraan dari metadata platform dan dapat berbeda setelah penggabungan.")


def format_speed(value: int | float | None) -> str:
    return f"{human_bytes(value)}/s" if value else "-"


def make_progress_widgets(label: str):
    st.markdown(f"### {label}")
    progress_bar = st.progress(0.0, text="Menyiapkan unduhan...")
    status_box = st.empty()
    log_box = st.empty()
    logs: list[str] = []

    def log_callback(message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        logs.append(clean)
        del logs[:-8]
        log_box.code("\n".join(logs), language=None)

    def progress_hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            ratio = min(downloaded / total, 1.0) if total else 0.05
            text = (
                f"Mengunduh {human_bytes(downloaded)} / {human_bytes(total)} · "
                f"{format_speed(data.get('speed'))} · ETA {data.get('eta', '-')} detik"
            )
            progress_bar.progress(ratio, text=text)
            status_box.info("Sedang mengunduh media...")
        elif status == "finished":
            progress_bar.progress(1.0, text="Data selesai diunduh. Memproses file...")
            status_box.info("Menyiapkan hasil akhir...")
        elif status == "error":
            status_box.error("Terjadi kegagalan saat mengunduh.")

    def postprocessor_hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        name = data.get("postprocessor") or "FFmpeg"
        if status == "started":
            status_box.info(f"Menjalankan proses: {name}")
        elif status == "finished":
            status_box.success(f"Proses {name} selesai.")

    return progress_bar, status_box, progress_hook, postprocessor_hook, log_callback


def render_browser_downloads(results: list[dict[str, Any]]) -> None:
    if not results:
        return

    st.subheader("Unduh hasil ke perangkat")
    for item_index, item in enumerate(results):
        if item.get("status") != "Berhasil":
            continue
        st.markdown(f"**{item.get('title', 'Media')}**")
        files = sorted(
            item.get("files", []),
            key=lambda file_info: (0 if Path(file_info["path"]).suffix.lower() == ".zip" else 1, file_info["name"]),
        )
        for file_index, file_info in enumerate(files):
            path = Path(file_info["path"])
            if not path.exists() or not path.is_file():
                st.warning(f"File sementara sudah tidak tersedia: {path.name}")
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            st.download_button(
                label=f"⬇️ {path.name} ({file_info.get('size', '-')})",
                data=path.read_bytes(),
                file_name=path.name,
                mime=mime,
                key=f"download_{item_index}_{file_index}_{path.stat().st_mtime_ns}",
                use_container_width=True,
            )


cleanup_old_sessions()

st.title("⬇️ Media Downloader")
st.caption("Video, audio, foto/carousel, dan Foto Live dari media publik yang Anda miliki atau diizinkan untuk disimpan.")

with st.expander("Batas penggunaan", expanded=False):
    st.markdown(
        """
        Aplikasi tidak dirancang untuk membobol DRM, paywall, akun privat, atau pembatasan akses.
        Ketersediaan dan kualitas format bergantung pada media yang benar-benar disediakan platform.
        Gunakan hanya untuk konten milik sendiri, domain publik, berlisensi bebas, atau yang telah diizinkan pemiliknya.
        """
    )

if CLOUD_MODE:
    st.info(
        "Mode server aktif. File diproses sementara lalu diunduh melalui tombol browser. "
        "Maksimal 3 URL per proses dan sekitar 300 MB per file."
    )

if "preview" not in st.session_state:
    st.session_state.preview = None
if "last_results" not in st.session_state:
    st.session_state.last_results = []

main_tab, guide_tab = st.tabs(["Unduh", "Panduan"])

with main_tab:
    url_limit = CLOUD_URL_LIMIT if CLOUD_MODE else LOCAL_URL_LIMIT
    url_text = st.text_area(
        "URL media",
        height=120,
        placeholder=f"Tempel satu URL per baris. Maksimal {url_limit} URL.",
        help="Mendukung URL publik YouTube, TikTok, dan Instagram.",
    )

    action_col1, action_col2 = st.columns([1, 5])
    with action_col1:
        preview_clicked = st.button("🔎 Pratinjau", use_container_width=True)
    with action_col2:
        st.caption("Pratinjau membaca URL pertama tanpa mengunduh file.")

    if preview_clicked:
        try:
            urls = parse_urls(url_text, limit=url_limit)
            if not urls:
                st.warning("Masukkan minimal satu URL.")
            else:
                with st.spinner("Membaca metadata media..."):
                    st.session_state.preview = preview_media(urls[0])
        except Exception as exc:
            st.session_state.preview = None
            st.error(f"Pratinjau gagal: {exc}")

    if st.session_state.preview:
        render_preview(st.session_state.preview)

    st.divider()
    left, right = st.columns(2)

    with left:
        output_kind_label = st.radio(
            "Jenis hasil",
            ["Video", "Audio", "Foto", "Foto Live"],
            horizontal=True,
        )

        container = "mp4"
        resolution_label = "Terbaik tersedia"
        quality_mode = "original"
        audio_format = "original"
        bitrate = "320"
        photo_archive = True
        live_photo_format = "bundle"
        live_photo_duration = 3

        if output_kind_label == "Video":
            quality_choice = st.selectbox(
                "Mode kualitas",
                ["Versi asli / terbaik tersedia", "HD 1080p", "HD 720p", "Pilih resolusi manual"],
            )
            quality_mode = "original" if quality_choice.startswith("Versi asli") else "hd"
            container = st.selectbox("Kontainer", ["mkv", "mp4"], format_func=str.upper)
            if quality_choice == "Versi asli / terbaik tersedia":
                resolution_label = "Terbaik tersedia"
                st.info("Mengambil kualitas tertinggi yang disediakan platform tanpa sengaja menurunkan resolusi.")
            elif quality_choice == "HD 1080p":
                resolution_label = "1080p"
                st.info("Membatasi hasil maksimal Full HD 1080p agar ukuran lebih ringan dan kompatibel.")
            elif quality_choice == "HD 720p":
                resolution_label = "720p"
                st.info("Membatasi hasil maksimal HD 720p.")
            else:
                resolution_options = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
                resolution_label = st.selectbox("Resolusi maksimum", resolution_options, index=2)
            if container == "mkv":
                st.caption("MKV paling aman untuk mempertahankan codec dan kualitas sumber terbaik.")
            else:
                st.caption("MP4 lebih kompatibel, tetapi codec sumber tertentu mungkin tidak didukung semua pemutar.")

        elif output_kind_label == "Audio":
            audio_choice = st.selectbox("Format audio", ["MP3", "Audio asli (M4A/Opus/WebM sesuai sumber)"])
            audio_format = "mp3" if audio_choice == "MP3" else "original"
            bitrate = (
                st.selectbox("Bitrate MP3", ["320", "256", "192", "128"], index=0)
                if audio_format == "mp3"
                else "320"
            )

        elif output_kind_label == "Foto":
            photo_quality = st.selectbox(
                "Mode kualitas foto",
                ["Versi asli / resolusi tertinggi platform", "HD maksimal 1920 piksel"],
            )
            quality_mode = "original" if photo_quality.startswith("Versi asli") else "hd"
            photo_archive = st.checkbox("Buat ZIP yang berisi semua foto", value=True)
            if quality_mode == "original":
                st.info("Foto disimpan pada resolusi tertinggi yang diberikan TikTok, Instagram, atau YouTube tanpa resize oleh aplikasi.")
            else:
                st.info("Foto diperkecil maksimal 1920 piksel pada sisi terpanjang untuk versi HD yang lebih ringan.")

        else:
            live_quality = st.selectbox(
                "Mode kualitas sumber",
                ["Versi asli / terbaik tersedia", "HD 1080p", "HD 720p"],
            )
            quality_mode = "original" if live_quality.startswith("Versi asli") else "hd"
            live_choice = st.selectbox(
                "Format Foto Live",
                ["Paket ZIP (JPG + MOV)", "WebP animasi"],
            )
            live_photo_format = "bundle" if live_choice.startswith("Paket") else "webp"
            live_photo_duration = st.selectbox("Durasi gerak", [3, 5, 10, 15], index=0, format_func=lambda value: f"{value} detik")
            if live_quality == "Versi asli / terbaik tersedia":
                resolution_label = "Terbaik tersedia"
            elif live_quality == "HD 1080p":
                resolution_label = "1080p"
            else:
                resolution_label = "720p"
            st.caption(
                "Foto Live dibuat dari URL video/Reel/TikTok video. Paket ZIP berisi gambar JPG dan klip MOV; "
                "WebP dapat bergerak langsung di aplikasi yang mendukungnya."
            )

    with right:
        if CLOUD_MODE:
            output_dir = get_session_download_dir()
            st.text_input("Penyimpanan server", value="Sementara — otomatis dihapus", disabled=True)
            ffmpeg_location_text = ""
        else:
            output_dir_text = st.text_input(
                "Folder penyimpanan",
                value=str(default_download_directory()),
                help="Path folder di laptop yang menjalankan Streamlit.",
            )
            output_dir = Path(output_dir_text).expanduser() if output_dir_text.strip() else Path()
            ffmpeg_location_text = st.text_input(
                "Lokasi FFmpeg (opsional)",
                value="",
                placeholder=r"Contoh: C:\ffmpeg\bin atau C:\ffmpeg\bin\ffmpeg.exe",
            )

        ffmpeg_ok, detected_path = detect_ffmpeg(ffmpeg_location_text or None)
        gallery_ok, gallery_version = detect_gallery_dl()
        ffmpeg_required = (
            output_kind_label in {"Video", "Foto Live"}
            or (output_kind_label == "Audio" and audio_format == "mp3")
        )
        gallery_required = output_kind_label == "Foto"

        if ffmpeg_ok:
            source_label = (
                "portable"
                if detected_path and ("imageio_ffmpeg" in detected_path or "media_downloader_ffmpeg" in detected_path)
                else "sistem"
            )
            st.success(f"FFmpeg terdeteksi ({source_label}): {detected_path}")
        elif ffmpeg_required:
            st.warning(
                "FFmpeg belum terdeteksi. Video, MP3, dan Foto Live memerlukannya. "
                "Pastikan packages.txt serta requirements.txt berada di root repository."
            )
        else:
            st.info("Mode yang dipilih tidak membutuhkan konversi FFmpeg.")

        if gallery_required:
            if gallery_ok:
                st.success(f"gallery-dl terdeteksi: {gallery_version}")
            else:
                st.warning("gallery-dl belum terdeteksi. Gunakan requirements.txt dari ZIP final.")

        consent = st.checkbox("Saya memiliki hak atau izin untuk mengunduh media tersebut.", value=False)

    download_clicked = st.button(
        "⬇️ Mulai Download",
        type="primary",
        use_container_width=True,
        disabled=not consent,
    )

    if download_clicked:
        try:
            urls = parse_urls(url_text, limit=url_limit)
            if not urls:
                raise ValueError("Masukkan minimal satu URL.")

            invalid = []
            for url in urls:
                valid, reason = validate_public_url(url)
                if not valid:
                    invalid.append(f"{url} — {reason}")
            if invalid:
                raise ValueError("URL tidak valid:\n" + "\n".join(invalid))

            if not CLOUD_MODE and not str(output_dir).strip():
                raise ValueError("Folder penyimpanan tidak boleh kosong.")

            resolution = None
            if output_kind_label in {"Video", "Foto Live"} and resolution_label != "Terbaik tersedia":
                resolution = int(resolution_label.rstrip("p"))

            output_kind_map = {
                "Video": "video",
                "Audio": "audio",
                "Foto": "photo",
                "Foto Live": "live_photo",
            }
            settings = DownloadSettings(
                output_kind=output_kind_map[output_kind_label],
                container=container,
                resolution=resolution,
                quality_mode=quality_mode,
                audio_format=audio_format,
                audio_bitrate=bitrate,
                output_dir=output_dir,
                ffmpeg_location=ffmpeg_location_text.strip() or detected_path,
                max_filesize=CLOUD_MAX_FILE_BYTES if CLOUD_MODE else None,
                photo_archive=photo_archive,
                live_photo_format=live_photo_format,
                live_photo_duration=live_photo_duration,
            )

            if ffmpeg_required and not ffmpeg_ok:
                raise RuntimeError(
                    "FFmpeg diperlukan untuk mode ini. Pastikan packages.txt dan requirements.txt berada di root repository, "
                    "lalu reboot aplikasi Streamlit Cloud."
                )
            if gallery_required and not gallery_ok:
                raise RuntimeError("gallery-dl belum terpasang. Upload requirements.txt dari ZIP final lalu reboot aplikasi.")

            if CLOUD_MODE:
                remove_session_files()
                settings = DownloadSettings(
                    output_kind=settings.output_kind,
                    container=settings.container,
                    resolution=settings.resolution,
                    audio_format=settings.audio_format,
                    audio_bitrate=settings.audio_bitrate,
                    output_dir=get_session_download_dir(),
                    ffmpeg_location=settings.ffmpeg_location,
                    max_filesize=settings.max_filesize,
                    photo_archive=settings.photo_archive,
                    live_photo_format=settings.live_photo_format,
                    live_photo_duration=settings.live_photo_duration,
                )

            all_results: list[dict[str, Any]] = []
            successes = 0

            for index, url in enumerate(urls, start=1):
                progress_bar, status_box, progress_hook, pp_hook, log_callback = make_progress_widgets(
                    f"Media {index} dari {len(urls)}"
                )
                try:
                    info, files = download_media(
                        url=url,
                        settings=settings,
                        progress_hook=progress_hook,
                        postprocessor_hook=pp_hook,
                        log_callback=log_callback,
                    )
                    progress_bar.progress(1.0, text="Selesai")
                    file_rows = summarize_files(files)
                    total_size_bytes = sum(path.stat().st_size for path in files if path.exists() and path.is_file())
                    actual_format = selected_format_summary(info)
                    status_box.success(
                        f"Berhasil: {info.get('title', 'media')} · Total {human_bytes(total_size_bytes)}"
                    )
                    all_results.append(
                        {
                            "url": url,
                            "title": info.get("title") or "Tanpa judul",
                            "status": "Berhasil",
                            "files": file_rows,
                            "format": actual_format,
                        }
                    )
                    successes += 1
                    st.dataframe([actual_format], use_container_width=True, hide_index=True)
                    if file_rows:
                        st.dataframe(file_rows, use_container_width=True, hide_index=True)
                    else:
                        st.info(f"File disimpan di: {settings.output_dir.resolve()}")
                except Exception as exc:
                    status_box.error(f"Gagal: {exc}")
                    all_results.append({"url": url, "title": "-", "status": f"Gagal: {exc}", "files": []})

            st.session_state.last_results = all_results
            if successes == len(urls):
                st.success(f"Semua {successes} media berhasil diproses.")
                st.balloons()
            else:
                st.warning(f"Berhasil {successes} dari {len(urls)} media.")

        except Exception as exc:
            st.error(str(exc))

    if st.session_state.last_results:
        if CLOUD_MODE:
            render_browser_downloads(st.session_state.last_results)
            if st.button("🗑️ Hapus file sementara", use_container_width=True):
                remove_session_files()
                st.success("File sementara telah dihapus.")
                st.rerun()
        else:
            with st.expander("Hasil proses terakhir", expanded=False):
                for item in st.session_state.last_results:
                    st.markdown(f"**{item['title']}** — {item['status']}")
                    for file_info in item["files"]:
                        st.markdown(f"`{file_info['name']}` — **{file_info['size']}**")
                        st.code(file_info["path"], language=None)

with guide_tab:
    st.subheader("Cara menggunakan")
    st.markdown(
        """
        1. Tempel URL publik YouTube, TikTok, atau Instagram, satu URL per baris.
        2. Pilih **Video**, **Audio**, **Foto**, atau **Foto Live**.
        3. **Foto** mengambil seluruh gambar carousel TikTok/Instagram; untuk YouTube, mode ini mengambil thumbnail terbaik.
        4. **Foto Live** memerlukan URL video dan menghasilkan ZIP JPG+MOV atau WebP animasi.
        5. Centang pernyataan izin, lalu klik **Mulai Download**.
        6. Pada versi online, klik tombol hasil untuk menyimpan file ke perangkat.
        """
    )

    st.subheader("Catatan kualitas dan server")
    st.markdown(
        """
        - **Versi asli / terbaik tersedia** mengambil kualitas tertinggi yang masih disediakan platform; ini bukan file master kamera sebelum diunggah.
        - **HD 1080p/720p** sengaja membatasi resolusi agar ukuran lebih ringan dan kompatibel.
        - Foto TikTok/Instagram diambil oleh **gallery-dl** pada versi resolusi tertinggi yang endpoint platform sediakan.
        - Paket **Foto Live JPG+MOV** adalah pasangan gambar dan klip gerak portabel. Sebagian perangkat iPhone mungkin memerlukan aplikasi impor Live Photo agar dikenali sebagai satu item Live Photo native.
        - Foto Live dibuat ulang dari video sehingga prosesnya melibatkan encoding; video biasa tetap dipertahankan tanpa sengaja melakukan re-encode ketika memungkinkan.
        - Gunakan **MKV** untuk peluang terbesar mempertahankan kombinasi codec video/audio terbaik.
        - Mode server membatasi file sekitar 300 MB. File besar lebih cocok dijalankan lokal, Render berbayar, atau VPS.
        - Video/post privat, berbayar, dilindungi DRM, dibatasi wilayah, atau membutuhkan login tidak diproses.
        - Instagram dan TikTok dapat mengubah endpoint kapan saja; perbarui `yt-dlp` dan `gallery-dl` bila ekstraksi mulai gagal.
        """
    )

    st.subheader("Versi komponen")
    gallery_ok, gallery_version = detect_gallery_dl()
    st.code(
        f"Streamlit: {st.__version__}\n"
        f"yt-dlp: {yt_dlp.version.__version__}\n"
        f"gallery-dl: {gallery_version if gallery_ok else 'tidak terpasang'}",
        language=None,
    )
