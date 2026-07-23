from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import yt_dlp


SUPPORTED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
)

MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".m4a",
    ".mp3",
    ".opus",
    ".ogg",
    ".aac",
    ".wav",
    ".flac",
}


@dataclass(frozen=True)
class DownloadSettings:
    output_kind: str  # "video" or "audio"
    container: str = "mp4"
    resolution: int | None = None
    audio_format: str = "mp3"  # "mp3" or "original"
    audio_bitrate: str = "320"
    output_dir: Path = Path("downloads")
    ffmpeg_location: str | None = None
    max_filesize: int | None = None


class StreamlitLogger:
    """Minimal yt-dlp logger that forwards useful messages to the UI."""

    def __init__(self, callback: Callable[[str], None] | None = None) -> None:
        self.callback = callback

    def _send(self, message: str) -> None:
        if self.callback and message:
            self.callback(message)

    def debug(self, message: str) -> None:
        # yt-dlp sends ordinary informational output through debug().
        if not message.startswith("[debug]"):
            self._send(message)

    def info(self, message: str) -> None:
        self._send(message)

    def warning(self, message: str) -> None:
        self._send(f"Peringatan: {message}")

    def error(self, message: str) -> None:
        self._send(f"Error: {message}")


def default_download_directory() -> Path:
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.exists() else Path.home()
    return base / "MediaDownloader"


def domain_is_supported(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in SUPPORTED_DOMAINS)


def validate_public_url(url: str) -> tuple[bool, str]:
    cleaned = url.strip()
    if not cleaned:
        return False, "URL kosong."

    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return False, "Format URL tidak valid."

    if parsed.scheme not in {"http", "https"}:
        return False, "URL harus diawali http:// atau https://."
    if not domain_is_supported(parsed.hostname):
        return False, "Domain tidak didukung. Gunakan URL YouTube, TikTok, atau Instagram."
    if parsed.username or parsed.password:
        return False, "URL yang memuat kredensial tidak diizinkan."
    return True, ""


def parse_urls(raw_text: str, limit: int = 20) -> list[str]:
    candidates = [line.strip() for line in raw_text.splitlines() if line.strip()]
    deduplicated = list(dict.fromkeys(candidates))
    if len(deduplicated) > limit:
        raise ValueError(f"Maksimal {limit} URL dalam satu proses.")
    return deduplicated


def human_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_filesize(fmt: dict[str, Any]) -> int | None:
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def estimate_format_sizes(formats: Iterable[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
    """Estimate final video sizes per resolution from yt-dlp metadata.

    Many platforms expose video and audio as separate streams. For video-only
    formats, the best available audio estimate is added when metadata provides it.
    Values remain estimates because manifests and post-processing can change size.
    """
    format_list = [fmt for fmt in formats if isinstance(fmt, dict)]
    audio_sizes = [
        size
        for fmt in format_list
        if fmt.get("vcodec") == "none"
        and fmt.get("acodec") not in {None, "none"}
        and (size := _format_filesize(fmt)) is not None
    ]
    best_audio_size = max(audio_sizes, default=0)

    per_height: dict[int, int] = {}
    for fmt in format_list:
        height = fmt.get("height")
        size = _format_filesize(fmt)
        if not isinstance(height, (int, float)) or not size:
            continue
        if fmt.get("vcodec") in {None, "none"}:
            continue

        has_audio = fmt.get("acodec") not in {None, "none"}
        estimated_total = size if has_audio else size + best_audio_size
        height_int = int(height)
        per_height[height_int] = max(per_height.get(height_int, 0), estimated_total)

    rows = [
        {"Resolusi": f"{height}p", "Perkiraan ukuran": human_bytes(size)}
        for height, size in sorted(per_height.items(), reverse=True)[:10]
    ]
    best_size = rows[0]["Perkiraan ukuran"] if rows else "Tidak tersedia"
    return rows, best_size


def detect_ffmpeg(custom_location: str | None = None) -> tuple[bool, str | None]:
    """Temukan FFmpeg dari input pengguna, PATH sistem, atau binary portable.

    Streamlit Community Cloud seharusnya memasang FFmpeg melalui ``packages.txt``.
    Binary dari ``imageio-ffmpeg`` digunakan sebagai fallback agar fungsi merge dan
    ekstraksi MP3 tetap tersedia ketika paket sistem belum terpasang atau belum
    terdeteksi pada PATH proses Streamlit.
    """
    if custom_location:
        candidate = Path(custom_location).expanduser()
        if candidate.is_file() and candidate.exists():
            return True, str(candidate.resolve())
        if candidate.is_dir():
            for name in ("ffmpeg.exe", "ffmpeg"):
                executable = candidate / name
                if executable.is_file():
                    # yt-dlp menerima direktori atau path executable. Mengembalikan
                    # executable membuat hasil deteksi lebih jelas di antarmuka.
                    return True, str(executable.resolve())

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return True, system_ffmpeg

    # Fallback portable yang ikut dipasang dari requirements.txt. Binary disalin
    # ke nama standar "ffmpeg" agar yt-dlp dapat menemukannya secara konsisten.
    try:
        import imageio_ffmpeg  # type: ignore

        bundled_ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).expanduser()
        if bundled_ffmpeg.is_file():
            portable_dir = Path(tempfile.gettempdir()) / "media_downloader_ffmpeg"
            portable_dir.mkdir(parents=True, exist_ok=True)
            target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            portable_ffmpeg = portable_dir / target_name

            needs_copy = (
                not portable_ffmpeg.exists()
                or portable_ffmpeg.stat().st_size != bundled_ffmpeg.stat().st_size
            )
            if needs_copy:
                shutil.copy2(bundled_ffmpeg, portable_ffmpeg)
            if os.name != "nt":
                portable_ffmpeg.chmod(0o755)
            return True, str(portable_ffmpeg.resolve())
    except (ImportError, OSError, RuntimeError):
        pass

    return False, None


def _height_filter(resolution: int | None) -> str:
    return f"[height<={resolution}]" if resolution else ""


def build_format_selector(settings: DownloadSettings) -> str:
    if settings.output_kind == "audio":
        return "bestaudio/best"

    height = _height_filter(settings.resolution)
    if settings.container == "mp4":
        # Prefer MP4/H.264-style streams plus M4A for broad device compatibility.
        # The final fallback keeps the download working on sites that expose only
        # a combined or non-MP4 stream.
        return (
            f"bestvideo{height}[ext=mp4]+bestaudio[ext=m4a]/"
            f"best{height}[ext=mp4]/bestvideo{height}+bestaudio/best{height}"
        )

    return f"bestvideo{height}+bestaudio/best{height}"


def _postprocessors(settings: DownloadSettings) -> list[dict[str, Any]]:
    processors: list[dict[str, Any]] = []

    if settings.output_kind == "audio":
        # Untuk audio asli, simpan stream sumber tanpa konversi atau remux.
        if settings.audio_format == "original":
            return processors

        audio_processor: dict[str, Any] = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": settings.audio_bitrate,
        }
        processors.append(audio_processor)
        processors.append({"key": "FFmpegMetadata", "add_metadata": True})
        return processors

    if settings.output_kind == "video":
        # Remux only; this does not intentionally re-encode the media streams.
        processors.append(
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": settings.container,
            }
        )
        processors.append({"key": "FFmpegMetadata", "add_metadata": True})

    return processors


def build_ydl_options(
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    output_dir = settings.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    options: dict[str, Any] = {
        "format": build_format_selector(settings),
        "paths": {"home": str(output_dir), "temp": str(output_dir / ".temp")},
        "outtmpl": {"default": "%(title).180B [%(id)s].%(ext)s"},
        "noplaylist": True,
        "playlist_items": "1",
        "windowsfilenames": True,
        "continuedl": True,
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": StreamlitLogger(log_callback),
        "postprocessors": _postprocessors(settings),
    }

    if settings.output_kind == "video":
        options["merge_output_format"] = settings.container

    if settings.ffmpeg_location:
        options["ffmpeg_location"] = settings.ffmpeg_location
    if settings.max_filesize:
        options["max_filesize"] = settings.max_filesize

    if progress_hook:
        options["progress_hooks"] = [progress_hook]
    if postprocessor_hook:
        options["postprocessor_hooks"] = [postprocessor_hook]

    return options


def preview_media(url: str) -> dict[str, Any]:
    valid, reason = validate_public_url(url)
    if not valid:
        raise ValueError(reason)

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("Metadata media tidak dapat dibaca.")

    formats = [fmt for fmt in info.get("formats", []) if isinstance(fmt, dict)]
    heights = sorted(
        {
            int(fmt["height"])
            for fmt in formats
            if isinstance(fmt.get("height"), (int, float))
        },
        reverse=True,
    )
    size_estimates, estimated_best_size = estimate_format_sizes(formats)

    return {
        "id": info.get("id"),
        "title": info.get("title") or "Tanpa judul",
        "uploader": info.get("uploader") or info.get("channel") or "-",
        "duration": human_duration(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor_key") or info.get("extractor") or "-",
        "available_heights": heights,
        "estimated_best_size": estimated_best_size,
        "size_estimates": size_estimates,
    }


def _collect_output_files(output_dir: Path, media_id: str | None, started_at: float) -> list[Path]:
    output_dir = output_dir.expanduser().resolve()
    candidates: list[Path] = []

    if media_id:
        safe_id = re.escape(str(media_id))
        id_pattern = re.compile(rf"\[{safe_id}\](?:\.[^.]+)+$", re.IGNORECASE)
        for path in output_dir.iterdir():
            if path.is_file() and id_pattern.search(path.name):
                candidates.append(path)

    if not candidates:
        for path in output_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                if path.stat().st_mtime >= started_at - 2:
                    candidates.append(path)
            except OSError:
                continue

    return sorted(set(candidates), key=lambda item: item.stat().st_mtime, reverse=True)


def download_media(
    url: str,
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    valid, reason = validate_public_url(url)
    if not valid:
        raise ValueError(reason)

    started_at = time.time()
    options = build_ydl_options(
        settings=settings,
        progress_hook=progress_hook,
        postprocessor_hook=postprocessor_hook,
        log_callback=log_callback,
    )

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

    if not isinstance(info, dict):
        raise RuntimeError("Unduhan selesai, tetapi informasi file tidak tersedia.")

    files = _collect_output_files(settings.output_dir, info.get("id"), started_at)
    return info, files


def summarize_files(paths: Iterable[Path]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for path in paths:
        try:
            size = human_bytes(path.stat().st_size)
        except OSError:
            size = "-"
        result.append({"name": path.name, "size": size, "path": str(path)})
    return result
