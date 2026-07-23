from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import streamlit as st
import yt_dlp

from downloader import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    DownloadSettings,
    default_download_directory,
    detect_ffmpeg,
    detect_gallery_dl,
    download_media,
    fetch_preview_image_bytes,
    human_bytes,
    parse_urls,
    preview_media,
    selected_format_summary,
    summarize_files,
    validate_public_url,
)


st.set_page_config(page_title="Media Downloader", page_icon="⬇️", layout="wide")

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False


def build_custom_css(dark: bool) -> str:
    if dark:
        palette = {
            "bg": "#14151F",
            "panel": "#1B1D2B",
            "panel_alt": "#20223333",
            "ink": "#EEEEF7",
            "muted": "#A8ACC4",
            "line": "#31344A",
            "iris": "#9B8CFB",
            "iris_soft": "#2A2650",
            "teal": "#33D6C0",
            "input_bg": "#232538",
        }
    else:
        palette = {
            "bg": "#FAFAFC",
            "panel": "#FFFFFF",
            "panel_alt": "#F6F4FD",
            "ink": "#1E2130",
            "muted": "#5B5F73",
            "line": "#E4E1F2",
            "iris": "#6C5CE7",
            "iris_soft": "#EFEBFD",
            "teal": "#12A594",
            "input_bg": "#FFFFFF",
        }
    p = palette
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');

:root {{
    --md-bg: {p['bg']};
    --md-panel: {p['panel']};
    --md-panel-alt: {p['panel_alt']};
    --md-ink: {p['ink']};
    --md-muted: {p['muted']};
    --md-line: {p['line']};
    --md-iris: {p['iris']};
    --md-iris-soft: {p['iris_soft']};
    --md-teal: {p['teal']};
    --md-input-bg: {p['input_bg']};
}}

html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}

/* Force our own background/text everywhere so the OS/browser dark-mode
   preference can never leave light-mode text unreadable on a dark
   background (or vice versa) -- the in-app toggle is the single source
   of truth for which palette is active. */
[data-testid="stAppViewContainer"], .main, body {{
    background: var(--md-bg) !important;
    color: var(--md-ink) !important;
}}
[data-testid="stHeader"] {{ background: transparent !important; }}

.block-container {{ padding-top: 2rem; max-width: 1180px; }}

p, span, div, label, li, .stMarkdown, .stCaption {{ color: var(--md-ink); }}
.stCaption, small, [data-testid="stCaptionContainer"] {{ color: var(--md-muted) !important; }}

/* Hero */
.md-hero {{ display:flex; align-items:center; gap:14px; margin-bottom: 0.15rem; }}
.md-hero .md-badge {{
    display:flex; align-items:center; justify-content:center;
    width: 46px; height: 46px; border-radius: 14px;
    background: linear-gradient(135deg, var(--md-iris), var(--md-teal));
    font-size: 22px; flex-shrink: 0;
}}
.md-hero h1 {{
    font-family: 'Sora', sans-serif; font-weight: 800; letter-spacing: -0.02em;
    font-size: 2.05rem; margin: 0; color: var(--md-ink) !important;
}}
.md-subtitle {{ color: var(--md-muted) !important; font-size: 0.98rem; margin: 0.15rem 0 1.3rem 62px; }}

/* Step headers */
.md-step {{ display:flex; align-items:center; gap:10px; margin: 0.4rem 0 0.7rem 0; }}
.md-step .md-step-num {{
    display:flex; align-items:center; justify-content:center;
    width: 26px; height: 26px; border-radius: 50%;
    background: var(--md-iris); color: white !important; font-weight: 700; font-size: 0.82rem;
    font-family: 'Sora', sans-serif; flex-shrink: 0;
}}
.md-step .md-step-title {{
    font-family: 'Sora', sans-serif; font-weight: 700; font-size: 1.12rem; color: var(--md-ink) !important;
}}
.md-step .md-step-hint {{ color: var(--md-muted) !important; font-size: 0.86rem; margin-left: 36px; }}

/* Chips used for media-type labelling in the guide */
.md-chip {{
    display:inline-flex; align-items:center; gap:6px;
    background: var(--md-iris-soft); color: var(--md-iris) !important;
    border-radius: 999px; padding: 4px 12px; font-size: 0.83rem; font-weight: 600;
    margin: 2px 6px 2px 0;
}}

/* Bordered containers -> soft cards */
div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 16px !important;
    border: 1px solid var(--md-line) !important;
    background: var(--md-panel) !important;
}}

/* Text areas / inputs / selects need explicit theming too, otherwise they
   inherit the browser's native dark/light control chrome. */
.stTextArea textarea, .stTextInput input, .stNumberInput input {{
    background: var(--md-input-bg) !important;
    color: var(--md-ink) !important;
    border-color: var(--md-line) !important;
}}
[data-baseweb="select"] > div {{
    background: var(--md-input-bg) !important;
    color: var(--md-ink) !important;
    border-color: var(--md-line) !important;
}}
[data-baseweb="popover"] {{ background: var(--md-panel) !important; }}
.stSlider [data-baseweb="slider"] {{ color: var(--md-iris); }}

/* Buttons */
.stButton > button, .stDownloadButton > button {{
    border-radius: 10px !important;
    font-weight: 600 !important;
    background: var(--md-panel) !important;
    color: var(--md-ink) !important;
    border: 1px solid var(--md-line) !important;
}}
.stButton > button[kind="primary"] {{
    background: linear-gradient(135deg, var(--md-iris), var(--md-teal)) !important;
    color: white !important;
    border: none !important;
}}

/* Radio pills */
div[role="radiogroup"] {{ gap: 6px; }}
div[role="radiogroup"] label {{
    background: var(--md-iris-soft); border-radius: 999px; padding: 6px 14px !important;
    margin-right: 4px; color: var(--md-ink) !important;
}}

/* Checkboxes / toggles */
.stCheckbox label, .stToggle label {{ color: var(--md-ink) !important; }}

/* Tabs */
.stTabs [data-baseweb="tab"] {{ font-weight: 600; font-family: 'Sora', sans-serif; color: var(--md-ink) !important; }}
.stTabs [data-baseweb="tab-panel"] {{ color: var(--md-ink); }}

/* Alert boxes keep Streamlit's own accessible colors; only round the corners */
[data-testid="stAlert"] {{ border-radius: 12px; }}

/* Code / dataframe panels */
.stCodeBlock, [data-testid="stTable"], [data-testid="stDataFrame"] {{
    background: var(--md-panel-alt) !important;
    border-radius: 12px;
}}

hr {{ border-color: var(--md-line) !important; }}
</style>
"""


st.markdown(build_custom_css(st.session_state.dark_mode), unsafe_allow_html=True)

_top_spacer, _top_toggle = st.columns([6, 1])
with _top_toggle:
    dark_choice = st.toggle(
        "🌙 Gelap" if st.session_state.dark_mode else "🌞 Terang",
        value=st.session_state.dark_mode,
        key="dark_mode_toggle",
        help="Ganti tampilan terang/gelap.",
    )
if dark_choice != st.session_state.dark_mode:
    st.session_state.dark_mode = dark_choice
    st.rerun()


def step_header(number: int, title: str, hint: str = "") -> None:
    hint_html = f'<div class="md-step-hint">{hint}</div>' if hint else ""
    st.markdown(
        f"""
        <div class="md-step">
            <div class="md-step-num">{number}</div>
            <div class="md-step-title">{title}</div>
        </div>
        {hint_html}
        """,
        unsafe_allow_html=True,
    )



CLOUD_MODE = os.getenv("MEDIA_DOWNLOADER_CLOUD", "").lower() in {"1", "true", "yes"} or os.name != "nt"
CLOUD_URL_LIMIT = 3
LOCAL_URL_LIMIT = 20
CLOUD_MAX_FILE_BYTES = 300 * 1024 * 1024
SESSION_ROOT = Path(tempfile.gettempdir()) / "media_downloader_sessions"


def cleanup_old_sessions(max_age_hours: int = 12) -> None:
    if not SESSION_ROOT.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
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


def render_preview(data: dict[str, Any], selected_mode: str) -> None:
    detected = data.get("detected_kind", "unknown")
    media_type = data.get("media_type", "-")

    st.subheader(data.get("title", "Tanpa judul"))
    meta1, meta2, meta3, meta4 = st.columns(4)
    meta1.metric("Jenis terdeteksi", media_type)
    meta2.metric("Platform", str(data.get("extractor", "-")))
    meta3.metric("Durasi", str(data.get("duration", "-")))
    meta4.metric("Perkiraan terbaik", str(data.get("estimated_best_size", "-")))

    if selected_mode == "Foto / Foto Live (Otomatis)":
        if detected == "photo":
            st.success("Pemilahan otomatis: posting terdeteksi sebagai FOTO. Hasil akan berupa JPG/PNG/WebP individual.")
        elif detected == "video":
            st.success("Pemilahan otomatis: posting terdeteksi sebagai VIDEO. Hasil akan dibuat menjadi Foto Live.")
        else:
            st.warning("Jenis media belum dapat dipastikan. Aplikasi akan mencoba foto terlebih dahulu, lalu Foto Live.")
    elif selected_mode == "Foto Live" and detected == "photo":
        st.info("Posting ini hanya berisi foto statis. Saat download, aplikasi otomatis memberikan foto individual agar tidak gagal.")
    elif selected_mode == "Foto" and detected == "video":
        st.info("URL ini berisi video. Mode Foto akan mengambil thumbnail dengan resolusi tertinggi.")

    preview_images = [url for url in data.get("preview_images", []) if url]
    preview_platform = data.get("preview_image_platform") or data.get("extractor") or "-"
    if preview_images:
        st.markdown(f"#### Pratinjau foto ({data.get('photo_count') or len(preview_images)} terdeteksi)")
        for start in range(0, len(preview_images), 3):
            columns = st.columns(3)
            for offset, image_url in enumerate(preview_images[start : start + 3]):
                with columns[offset]:
                    image_bytes = fetch_preview_image_bytes(image_url, str(preview_platform))
                    shown = False
                    if image_bytes:
                        try:
                            st.image(image_bytes, caption=f"Foto {start + offset + 1}", use_container_width=True)
                            shown = True
                        except Exception:
                            shown = False
                    if not shown:
                        st.warning(
                            f"Foto {start + offset + 1}: CDN menolak permintaan pratinjau "
                            "(kemungkinan link privat atau kadaluarsa). Coba tetap unduh — proses "
                            "download memakai header yang sama dan sering tetap berhasil."
                        )
    else:
        left, right = st.columns([1, 2])
        with left:
            if data.get("thumbnail"):
                st.image(data["thumbnail"], caption="Thumbnail / bingkai pratinjau", use_container_width=True)
            else:
                st.info("Gambar pratinjau tidak tersedia.")
        with right:
            st.write(f"**Pengunggah:** {data.get('uploader', '-')}")
            heights = data.get("available_heights") or []
            resolution_text = ", ".join(f"{height}p" for height in heights[:10]) or "Tidak tersedia"
            st.write(f"**Resolusi video tersedia:** {resolution_text}")
            if data.get("preview_video_url") and selected_mode in {"Foto Live", "Foto / Foto Live (Otomatis)", "Video"}:
                with st.expander("Putar pratinjau video", expanded=False):
                    try:
                        st.video(data["preview_video_url"])
                    except Exception:
                        st.info("Pemutar browser tidak dapat membuka stream pratinjau, tetapi download tetap dapat dicoba.")

    size_estimates = data.get("size_estimates") or []
    if size_estimates:
        with st.expander("Perkiraan ukuran per resolusi", expanded=False):
            st.dataframe(size_estimates, use_container_width=True, hide_index=True)
            st.caption("Ukuran dapat berubah setelah stream video dan audio digabungkan.")

    if data.get("preview_error"):
        st.warning(f"Catatan deteksi: {data['preview_error']}")


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
            status_box.info("Sedang mengambil media...")
        elif status == "finished":
            progress_bar.progress(1.0, text="Data selesai diunduh. Menyiapkan hasil...")
            status_box.info("Menyiapkan file akhir...")
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


def _download_button(path: Path, label: str, key: str) -> None:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        key=key,
        use_container_width=True,
    )


def _render_image_cards(files: list[dict[str, str]], item_index: int) -> None:
    for start in range(0, len(files), 3):
        columns = st.columns(3)
        for offset, file_info in enumerate(files[start : start + 3]):
            path = Path(file_info["path"])
            with columns[offset]:
                try:
                    st.image(str(path), caption=f"{path.name} · {file_info['size']}", use_container_width=True)
                except Exception:
                    st.info(f"Pratinjau tidak tersedia untuk {path.name}")
                _download_button(
                    path,
                    f"⬇️ Download foto {start + offset + 1} ({file_info['size']})",
                    f"img_{item_index}_{start}_{offset}_{path.stat().st_mtime_ns}",
                )


def render_browser_downloads(results: list[dict[str, Any]]) -> None:
    successful = [item for item in results if item.get("status") == "Berhasil"]
    if not successful:
        return

    st.divider()
    st.header("Hasil download")
    st.caption("Foto, JPG Foto Live, MOV, video, dan audio dapat diunduh satu per satu. ZIP hanya pilihan tambahan.")

    for item_index, item in enumerate(successful):
        with st.container(border=True):
            st.subheader(item.get("title", "Media"))
            if item.get("note") and item["note"] != "-":
                st.info(item["note"])

            valid_files = []
            for file_info in item.get("files", []):
                path = Path(file_info["path"])
                if path.exists() and path.is_file():
                    valid_files.append(file_info)
            if not valid_files:
                st.warning("File sementara sudah tidak tersedia.")
                continue

            image_files = [file for file in valid_files if Path(file["path"]).suffix.lower() in IMAGE_EXTENSIONS]
            video_files = [file for file in valid_files if Path(file["path"]).suffix.lower() in VIDEO_EXTENSIONS]
            audio_files = [file for file in valid_files if Path(file["path"]).suffix.lower() in AUDIO_EXTENSIONS]
            zip_files = [file for file in valid_files if Path(file["path"]).suffix.lower() == ".zip"]

            if image_files:
                st.markdown("#### Foto individual")
                _render_image_cards(image_files, item_index)

            if video_files:
                st.markdown("#### Video / klip Foto Live")
                for file_index, file_info in enumerate(video_files):
                    path = Path(file_info["path"])
                    try:
                        st.video(str(path))
                    except Exception:
                        st.info(f"Pratinjau video tidak tersedia untuk {path.name}")
                    _download_button(
                        path,
                        f"⬇️ Download {path.name} ({file_info['size']})",
                        f"video_{item_index}_{file_index}_{path.stat().st_mtime_ns}",
                    )

            if audio_files:
                st.markdown("#### Audio")
                for file_index, file_info in enumerate(audio_files):
                    path = Path(file_info["path"])
                    try:
                        st.audio(str(path))
                    except Exception:
                        pass
                    _download_button(
                        path,
                        f"⬇️ Download {path.name} ({file_info['size']})",
                        f"audio_{item_index}_{file_index}_{path.stat().st_mtime_ns}",
                    )

            if zip_files:
                with st.expander("Download semua dalam ZIP (opsional)", expanded=False):
                    for file_index, file_info in enumerate(zip_files):
                        path = Path(file_info["path"])
                        _download_button(
                            path,
                            f"📦 Download ZIP — {path.name} ({file_info['size']})",
                            f"zip_{item_index}_{file_index}_{path.stat().st_mtime_ns}",
                        )


cleanup_old_sessions()

st.markdown(
    """
    <div class="md-hero">
        <div class="md-badge">⬇️</div>
        <h1>Media Downloader</h1>
    </div>
    <p class="md-subtitle">Simpan video, audio, foto/carousel, dan Foto Live dari media publik yang Anda miliki atau diizinkan untuk diunduh.</p>
    """,
    unsafe_allow_html=True,
)

with st.expander("📜 Batas penggunaan", expanded=False):
    st.markdown(
        """
        Aplikasi tidak membobol DRM, paywall, akun privat, atau pembatasan akses. Kualitas hasil bergantung pada
        file yang masih disediakan platform. Gunakan hanya untuk konten milik sendiri, domain publik,
        berlisensi bebas, atau yang telah diizinkan pemiliknya.
        """
    )

if CLOUD_MODE:
    st.info("Mode server aktif. Maksimal 3 URL per proses dan sekitar 300 MB per file. File bersifat sementara.")

if "preview" not in st.session_state:
    st.session_state.preview = None
if "preview_url" not in st.session_state:
    st.session_state.preview_url = ""
if "last_results" not in st.session_state:
    st.session_state.last_results = []

main_tab, guide_tab = st.tabs(["Unduh", "Panduan"])

with main_tab:
    url_limit = CLOUD_URL_LIMIT if CLOUD_MODE else LOCAL_URL_LIMIT
    with st.container(border=True):
        step_header(1, "Tempel URL", f"Satu URL per baris · maksimal {url_limit} URL")
        url_text = st.text_area(
            "URL media",
            height=120,
            placeholder=f"Tempel satu URL per baris. Maksimal {url_limit} URL.",
            help="Mendukung URL publik YouTube, TikTok, dan Instagram.",
            label_visibility="collapsed",
        )

    st.write("")
    with st.container(border=True):
        step_header(2, "Pilih jenis hasil")
        output_kind_label = st.radio(
            "Jenis hasil",
            ["Video", "Audio", "Foto / Foto Live (Otomatis)", "Foto", "Foto Live"],
            horizontal=True,
            label_visibility="collapsed",
            format_func=lambda value: {
                "Video": "🎬 Video",
                "Audio": "🎵 Audio",
                "Foto / Foto Live (Otomatis)": "✨ Otomatis (Foto/Foto Live)",
                "Foto": "🖼️ Foto",
                "Foto Live": "🎞️ Foto Live",
            }.get(value, value),
        )

        preview_col, hint_col = st.columns([1, 5])
        with preview_col:
            preview_clicked = st.button("🔎 Pratinjau", use_container_width=True)
        with hint_col:
            st.caption("Pratinjau mendeteksi otomatis apakah URL berisi foto/carousel atau video.")

    if preview_clicked:
        try:
            urls = parse_urls(url_text, limit=url_limit)
            if not urls:
                st.warning("Masukkan minimal satu URL.")
            else:
                with st.spinner("Mendeteksi jenis media dan menyiapkan pratinjau..."):
                    st.session_state.preview = preview_media(urls[0])
                    st.session_state.preview_url = urls[0]
        except Exception as exc:
            st.session_state.preview = None
            st.error(f"Pratinjau gagal: {exc}")

    if st.session_state.preview and st.session_state.preview_url.strip() in url_text:
        st.write("")
        with st.container(border=True):
            render_preview(st.session_state.preview, output_kind_label)

    st.write("")
    st.write("")
    with st.container(border=True):
        step_header(3, "Atur kualitas dan tujuan penyimpanan")
        left, right = st.columns(2)

        container = "mkv"
        resolution_label = "Terbaik tersedia"
        quality_mode = "original"
        audio_format = "original"
        bitrate = "320"
        photo_archive = False
        photo_max_dimension = 1920
        live_photo_format = "bundle"
        live_photo_duration = 3
        live_photo_archive = True
        photo_live_seconds = 2.5

        with left:
            if output_kind_label == "Video":
                quality_choice = st.selectbox(
                    "Mode kualitas video",
                    ["Versi asli / terbaik tersedia", "HD 1080p", "HD 720p", "Pilih resolusi manual"],
                )
                container = st.selectbox("Kontainer", ["mkv", "mp4"], format_func=str.upper)
                if quality_choice == "HD 1080p":
                    resolution_label = "1080p"
                    quality_mode = "hd"
                elif quality_choice == "HD 720p":
                    resolution_label = "720p"
                    quality_mode = "hd"
                elif quality_choice == "Pilih resolusi manual":
                    resolution_label = st.selectbox(
                        "Resolusi maksimum",
                        ["2160p", "1440p", "1080p", "720p", "480p", "360p"],
                        index=2,
                    )
                    quality_mode = "hd"
                st.caption("MKV paling aman untuk mempertahankan kombinasi codec sumber terbaik.")

            elif output_kind_label == "Audio":
                audio_choice = st.selectbox("Format audio", ["Audio asli", "MP3"])
                audio_format = "original" if audio_choice == "Audio asli" else "mp3"
                if audio_format == "mp3":
                    bitrate = st.selectbox("Bitrate MP3", ["320", "256", "192", "128"], index=0)

            elif output_kind_label == "Foto":
                photo_quality = st.selectbox(
                    "Mode kualitas foto",
                    ["Versi asli / resolusi tertinggi platform", "HD maksimal 1920 piksel", "HD ringan maksimal 1280 piksel"],
                )
                if photo_quality.startswith("HD maksimal"):
                    quality_mode = "hd"
                    photo_max_dimension = 1920
                elif photo_quality.startswith("HD ringan"):
                    quality_mode = "hd"
                    photo_max_dimension = 1280
                photo_archive = st.checkbox("Tambahkan ZIP semua foto", value=False)
                st.success("Setiap foto tetap diberikan sebagai JPG/PNG/WebP individual. ZIP tidak menggantikan foto individual.")

            else:
                auto_mode = output_kind_label == "Foto / Foto Live (Otomatis)"
                source_quality = st.selectbox(
                    "Kualitas sumber",
                    ["Versi asli / terbaik tersedia", "HD 1080p / foto 1920px", "HD 720p / foto 1280px"],
                )
                if source_quality.startswith("HD 1080"):
                    quality_mode = "hd"
                    resolution_label = "1080p"
                    photo_max_dimension = 1920
                elif source_quality.startswith("HD 720"):
                    quality_mode = "hd"
                    resolution_label = "720p"
                    photo_max_dimension = 1280

                live_choice = st.selectbox("Format Foto Live untuk sumber VIDEO", ["JPG + MOV", "WebP animasi"])
                live_photo_format = "bundle" if live_choice == "JPG + MOV" else "webp"
                photo_live_seconds = st.slider(
                    "Durasi tiap foto pada video Foto Live (jika URL berupa carousel foto)",
                    min_value=1.0,
                    max_value=6.0,
                    value=2.5,
                    step=0.5,
                )
                photo_archive = st.checkbox("Tambahkan ZIP semua foto jika posting berupa carousel", value=False)
                live_photo_archive = (
                    st.checkbox("Tambahkan ZIP pasangan JPG + MOV", value=True)
                    if live_photo_format == "bundle"
                    else False
                )
                if auto_mode:
                    st.success(
                        "Otomatis: posting foto → foto individual TETAP diberikan, DAN aplikasi juga mencoba "
                        "membuat video Foto Live (foto + musik latar asli posting) jika ada musiknya. "
                        "Posting video → dibuat menjadi Foto Live (JPG + MOV/WebP)."
                    )
                else:
                    st.info(
                        "Jika URL ternyata posting foto statis, aplikasi memberikan foto individual DAN mencoba "
                        "membuat video Foto Live (foto + musik latar asli posting, kualitas terbaik yang tersedia) "
                        "sekaligus — bukan salah satu saja."
                    )

        with right:
            if CLOUD_MODE:
                output_dir = get_session_download_dir()
                st.text_input("Penyimpanan server", value="Sementara — otomatis dihapus", disabled=True)
                ffmpeg_location_text = ""
            else:
                output_dir_text = st.text_input("Folder penyimpanan", value=str(default_download_directory()))
                output_dir = Path(output_dir_text).expanduser() if output_dir_text.strip() else Path()
                ffmpeg_location_text = st.text_input(
                    "Lokasi FFmpeg (opsional)",
                    value="",
                    placeholder=r"Contoh: C:\ffmpeg\bin atau C:\ffmpeg\bin\ffmpeg.exe",
                )

            ffmpeg_ok, detected_path = detect_ffmpeg(ffmpeg_location_text or None)
            gallery_ok, gallery_version = detect_gallery_dl()

            ffmpeg_definitely_required = (
                output_kind_label in {"Video", "Foto Live"}
                or (output_kind_label == "Audio" and audio_format == "mp3")
            )
            ffmpeg_maybe_required = output_kind_label == "Foto / Foto Live (Otomatis)"

            if ffmpeg_ok:
                source_label = "portable" if detected_path and "media_downloader_ffmpeg" in detected_path else "sistem"
                st.success(f"FFmpeg terdeteksi ({source_label}): {detected_path}")
            elif ffmpeg_definitely_required:
                st.warning("FFmpeg belum terdeteksi. Mode yang dipilih dapat gagal.")
            elif ffmpeg_maybe_required:
                st.warning("FFmpeg belum terdeteksi. Posting foto masih bisa, tetapi pembuatan Foto Live tidak bisa.")
            else:
                st.info("FFmpeg tidak wajib untuk pilihan saat ini.")

            if gallery_ok:
                st.success(f"gallery-dl terdeteksi: {gallery_version}")
            elif output_kind_label in {"Foto", "Foto Live", "Foto / Foto Live (Otomatis)"}:
                st.warning("gallery-dl belum terdeteksi. Posting foto TikTok/Instagram tidak dapat diproses.")

            consent = st.checkbox("Saya memiliki hak atau izin untuk mengunduh media tersebut.", value=False)

    st.write("")
    with st.container(border=True):
        step_header(4, "Mulai unduhan")
        download_clicked = st.button(
            "⬇️ Mulai Download",
            type="primary",
            use_container_width=True,
            disabled=not consent,
        )
        if not consent:
            st.caption("Centang kotak persetujuan pada Langkah 3 untuk mengaktifkan tombol ini.")

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
            if output_kind_label in {"Video", "Foto Live", "Foto / Foto Live (Otomatis)"} and resolution_label != "Terbaik tersedia":
                resolution = int(resolution_label.rstrip("p"))

            output_kind_map = {
                "Video": "video",
                "Audio": "audio",
                "Foto / Foto Live (Otomatis)": "auto_photo_live",
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
                photo_max_dimension=photo_max_dimension,
                live_photo_format=live_photo_format,
                live_photo_duration=live_photo_duration,
                live_photo_archive=live_photo_archive,
                photo_live_seconds_per_photo=photo_live_seconds,
            )

            if ffmpeg_definitely_required and not ffmpeg_ok:
                raise RuntimeError("FFmpeg diperlukan untuk mode ini. Pastikan packages.txt berada di root repository lalu reboot aplikasi.")
            if output_kind_label == "Foto" and not gallery_ok and not all("youtube" in url or "youtu.be" in url for url in urls):
                raise RuntimeError("gallery-dl diperlukan untuk foto TikTok/Instagram. Pastikan requirements.txt versi final digunakan.")

            if CLOUD_MODE:
                remove_session_files()
                settings = replace(settings, output_dir=get_session_download_dir())

            all_results: list[dict[str, Any]] = []
            successes = 0

            for index, url in enumerate(urls, start=1):
                progress_bar, status_box, progress_hook, pp_hook, log_callback = make_progress_widgets(
                    f"Media {index} dari {len(urls)}"
                )
                job_dir = settings.output_dir / f"job_{index}_{uuid.uuid4().hex[:8]}"
                job_settings = replace(settings, output_dir=job_dir)
                try:
                    info, files = download_media(
                        url=url,
                        settings=job_settings,
                        progress_hook=progress_hook,
                        postprocessor_hook=pp_hook,
                        log_callback=log_callback,
                    )
                    progress_bar.progress(1.0, text="Selesai")
                    file_rows = summarize_files(files)
                    total_size_bytes = sum(path.stat().st_size for path in files if path.exists() and path.is_file())
                    actual_format = selected_format_summary(info)
                    note = str(info.get("_fallback_reason") or "-")
                    status_box.success(f"Berhasil: {info.get('title', 'media')} · Total {human_bytes(total_size_bytes)}")
                    all_results.append(
                        {
                            "url": url,
                            "title": info.get("title") or "Tanpa judul",
                            "status": "Berhasil",
                            "files": file_rows,
                            "format": actual_format,
                            "note": note,
                        }
                    )
                    successes += 1
                    st.dataframe([actual_format], use_container_width=True, hide_index=True)
                    if file_rows:
                        st.dataframe(file_rows, use_container_width=True, hide_index=True)
                except Exception as exc:
                    status_box.error(f"Gagal: {exc}")
                    all_results.append({"url": url, "title": "-", "status": f"Gagal: {exc}", "files": [], "note": "-"})

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
            with st.expander("Hasil proses terakhir", expanded=True):
                for item in st.session_state.last_results:
                    st.markdown(f"**{item['title']}** — {item['status']}")
                    if item.get("note") and item["note"] != "-":
                        st.info(item["note"])
                    for file_info in item["files"]:
                        st.markdown(f"`{file_info['name']}` — **{file_info['size']}**")
                        st.code(file_info["path"], language=None)

with guide_tab:
    st.markdown(
        """
        <span class="md-chip">🎬 Video</span>
        <span class="md-chip">🎵 Audio</span>
        <span class="md-chip">🖼️ Foto</span>
        <span class="md-chip">🎞️ Foto Live</span>
        <span class="md-chip">✨ Otomatis</span>
        """,
        unsafe_allow_html=True,
    )
    st.write("")

    st.subheader("Perubahan versi final")
    st.markdown(
        """
        - **Foto tidak lagi hanya ZIP.** Setiap JPG/PNG/WebP selalu ditampilkan dan memiliki tombol download sendiri.
        - ZIP hanyalah pilihan tambahan melalui checkbox.
        - Pratinjau posting foto menampilkan beberapa gambar carousel sebelum download.
        - Pratinjau Foto Live menampilkan thumbnail dan, bila didukung browser, video sumber.
        - Mode **Foto / Foto Live (Otomatis)** membedakan posting foto dan video secara otomatis.
        - Jika mode Foto Live diberi URL posting foto statis, aplikasi otomatis mengunduh foto individual agar tidak error.
        - Hasil Foto Live **JPG dan MOV** memiliki tombol download masing-masing; ZIP pasangan bersifat opsional.
        - Carousel TikTok campuran (foto biasa + Foto Live) kini dideteksi per item secara otomatis.
        """
    )

    st.subheader("Cara menggunakan")
    st.markdown(
        """
        1. **Tempel URL** publik YouTube, TikTok, atau Instagram.
        2. Untuk foto dan Foto Live, pilih **✨ Otomatis (Foto/Foto Live)**.
        3. Klik **🔎 Pratinjau** untuk memastikan hasil deteksi.
        4. Pilih kualitas dan format hasil.
        5. Klik **⬇️ Mulai Download**.
        6. Gunakan tombol di bawah setiap foto, JPG, MOV, video, atau audio untuk download satu per satu.
        """
    )

    st.subheader("Versi komponen")
    gallery_ok, gallery_version = detect_gallery_dl()
    st.code(
        f"Aplikasi: v7\n"
        f"Streamlit: {st.__version__}\n"
        f"yt-dlp: {yt_dlp.version.__version__}\n"
        f"gallery-dl: {gallery_version if gallery_ok else 'tidak terpasang'}",
        language=None,
    )
