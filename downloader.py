from __future__ import annotations

import mimetypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yt_dlp
from yt_dlp.utils import download_range_func


SUPPORTED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
)

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".heic",
    ".heif",
    ".gif",
}

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".opus", ".ogg", ".aac", ".wav", ".flac"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | {".zip"}


@dataclass(frozen=True)
class DownloadSettings:
    output_kind: str  # video, audio, photo, live_photo, auto_photo_live
    container: str = "mp4"
    resolution: int | None = None
    quality_mode: str = "original"
    audio_format: str = "mp3"
    audio_bitrate: str = "320"
    output_dir: Path = Path("downloads")
    ffmpeg_location: str | None = None
    max_filesize: int | None = None
    photo_archive: bool = False
    photo_max_dimension: int = 1920
    live_photo_format: str = "bundle"  # bundle or webp
    live_photo_duration: int = 3
    live_photo_archive: bool = True
    clip_duration: float | None = None
    photo_live_seconds_per_photo: float = 2.5


class StreamlitLogger:
    def __init__(self, callback: Callable[[str], None] | None = None) -> None:
        self.callback = callback

    def _send(self, message: str) -> None:
        if self.callback and message:
            self.callback(message)

    def debug(self, message: str) -> None:
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


def _safe_filename(value: str, fallback: str = "media") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:150] or fallback).strip()


def _platform_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtube" in host or host == "youtu.be":
        return "YouTube"
    if "tiktok" in host:
        return "TikTok"
    if "instagram" in host:
        return "Instagram"
    return host or "Platform"


def detect_ffmpeg(custom_location: str | None = None) -> tuple[bool, str | None]:
    if custom_location:
        candidate = Path(custom_location).expanduser()
        if candidate.is_file() and candidate.exists():
            return True, str(candidate.resolve())
        if candidate.is_dir():
            for name in ("ffmpeg.exe", "ffmpeg"):
                executable = candidate / name
                if executable.is_file():
                    return True, str(executable.resolve())

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return True, system_ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore

        bundled_ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).expanduser()
        if bundled_ffmpeg.is_file():
            portable_dir = Path(tempfile.gettempdir()) / "media_downloader_ffmpeg"
            portable_dir.mkdir(parents=True, exist_ok=True)
            target = portable_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if not target.exists() or target.stat().st_size != bundled_ffmpeg.stat().st_size:
                shutil.copy2(bundled_ffmpeg, target)
            if os.name != "nt":
                target.chmod(0o755)
            return True, str(target.resolve())
    except (ImportError, OSError, RuntimeError):
        pass
    return False, None


def detect_gallery_dl() -> tuple[bool, str | None]:
    try:
        import gallery_dl  # type: ignore

        return True, str(getattr(gallery_dl, "__version__", "terpasang"))
    except ImportError:
        return False, None


def _format_filesize(fmt: dict[str, Any]) -> int | None:
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def estimate_format_sizes(formats: Iterable[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
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
        if not isinstance(height, (int, float)) or not size or fmt.get("vcodec") in {None, "none"}:
            continue
        total = size if fmt.get("acodec") not in {None, "none"} else size + best_audio_size
        per_height[int(height)] = max(per_height.get(int(height), 0), total)
    rows = [
        {"Resolusi": f"{height}p", "Perkiraan ukuran": human_bytes(size)}
        for height, size in sorted(per_height.items(), reverse=True)[:10]
    ]
    return rows, rows[0]["Perkiraan ukuran"] if rows else "Tidak tersedia"


def _gallery_common_args() -> list[str]:
    # Ambil foto DAN video/foto-live dalam satu proses. Sebelumnya video pada
    # carousel campuran (mis. Instagram carousel berisi foto + Reels/Foto
    # Live) sengaja dibuang oleh filter ini sehingga hanya .jpg yang pernah
    # muncul. Sekarang setiap item carousel diambil apa adanya: item foto
    # tetap .jpg/.png/.webp, item video/Foto Live otomatis menjadi .mp4/.mov.
    media_filter = (
        "extension and extension.lower() in ("
        "'jpg','jpeg','png','webp','avif','heic','heif','gif',"
        "'mp4','mov','webm','mkv')"
    )
    return [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "--no-colors",
        "--windows-filenames",
        "--filter",
        media_filter,
        "-o",
        "output.fallback=false",
        "-o",
        "extractor.tiktok.photos=true",
        "-o",
        "extractor.tiktok.audio=false",
        "-o",
        "extractor.tiktok.covers=false",
        "-o",
        "extractor.instagram.videos=true",
    ]


def _gallery_audio_args() -> list[str]:
    """gallery-dl invocation that fetches ONLY the background music/audio of a
    TikTok photo post (used to build the 'Foto Live' photo+music video)."""
    audio_filter = "extension and extension.lower() in ('mp3','m4a','aac','wav','opus','ogg')"
    return [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "--no-colors",
        "--windows-filenames",
        "--filter",
        audio_filter,
        "-o",
        "output.fallback=false",
        "-o",
        "extractor.tiktok.photos=false",
        "-o",
        "extractor.tiktok.audio=true",
        "-o",
        "extractor.tiktok.covers=false",
    ]


def _download_post_audio(url: str, output_dir: Path, timeout: int = 60) -> Path | None:
    """Best-effort download of the original background music of a TikTok photo
    post. Returns the audio file path, or None if unavailable (e.g. licensed
    music that TikTok blocks from direct download, or an Instagram post)."""
    if _platform_name(url) != "TikTok" or not detect_gallery_dl()[0]:
        return None
    audio_dir = output_dir / f".audio_{uuid.uuid4().hex}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    command = _gallery_audio_args() + ["--directory", str(audio_dir), "--filename", "/O", url]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    audio_files = [
        path for path in audio_dir.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return max(audio_files, key=lambda path: path.stat().st_size) if audio_files else None


def _gallery_image_urls(url: str, timeout: int = 45) -> tuple[list[str], str | None]:
    """Return (image_urls, error_detail). error_detail is None on a clean run."""
    if not detect_gallery_dl()[0]:
        return [], "gallery-dl tidak terpasang di server."
    if _platform_name(url) == "YouTube":
        return [], None
    command = _gallery_common_args() + ["--get-urls", url]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], f"gallery-dl timeout setelah {timeout} detik saat mengambil URL foto."
    except OSError as exc:
        return [], f"gallery-dl gagal dijalankan: {exc}"

    lines = []
    for raw in (process.stdout or "").splitlines():
        value = raw.strip()
        if value.startswith(("http://", "https://")):
            lines.append(value)
    deduped = list(dict.fromkeys(lines))

    if not deduped:
        stderr_tail = (process.stderr or "").strip()
        if not stderr_tail:
            stderr_tail = (process.stdout or "").strip()
        detail = stderr_tail[-500:] if stderr_tail else f"gallery-dl keluar dengan kode {process.returncode} tanpa output."
        return [], detail

    return deduped, None


TIKTOK_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}

INSTAGRAM_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.instagram.com/",
}


def _preview_headers_for_platform(platform: str) -> dict[str, str]:
    if platform == "TikTok":
        return TIKTOK_IMAGE_HEADERS
    if platform == "Instagram":
        return INSTAGRAM_IMAGE_HEADERS
    return {"User-Agent": TIKTOK_IMAGE_HEADERS["User-Agent"]}


def _looks_like_image(data: bytes, content_type: str | None) -> bool:
    if content_type and not content_type.split(";", 1)[0].strip().lower().startswith("image/"):
        return False
    if len(data) < 12:
        return False
    signatures = (
        b"\xff\xd8\xff",  # JPEG
        b"\x89PNG\r\n\x1a\n",  # PNG
        b"GIF87a",
        b"GIF89a",
        b"RIFF",  # WEBP (RIFF....WEBP)
        b"\x00\x00\x00\x18ftypavif",  # AVIF (rough)
        b"\x00\x00\x00\x1cftypavif",
        b"\x00\x00\x00 ftypavif",
    )
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if b"ftypavif" in data[:32] or b"ftypheic" in data[:32]:
        return True
    return False


def fetch_preview_image_bytes(media_url: str, platform: str, timeout: int = 20) -> bytes | None:
    """Download an image server-side (with the right Referer) so it can be shown
    in the UI even though the CDN blocks hotlinking / direct browser access.
    Returns None if the response isn't actually a decodable image (e.g. TikTok
    returned an HTML error/captcha page instead of the photo)."""
    headers = _preview_headers_for_platform(platform)
    request = Request(media_url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type")
            data = response.read()
    except Exception:
        return None
    if not data or not _looks_like_image(data, content_type):
        return None
    return data


def _extract_ydl_info(url: str) -> dict[str, Any]:
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
    entries = info.get("entries")
    if isinstance(entries, list) and entries:
        first = next((item for item in entries if isinstance(item, dict)), None)
        if first:
            info = {**info, **first}
    return info


def _best_progressive_preview_url(formats: list[dict[str, Any]]) -> str | None:
    candidates = [
        fmt
        for fmt in formats
        if fmt.get("url")
        and fmt.get("vcodec") not in {None, "none"}
        and fmt.get("acodec") not in {None, "none"}
        and fmt.get("protocol") not in {"m3u8", "m3u8_native"}
    ]
    if not candidates:
        candidates = [fmt for fmt in formats if fmt.get("url") and fmt.get("vcodec") not in {None, "none"}]
    if not candidates:
        return None
    selected = max(candidates, key=lambda fmt: (int(fmt.get("height") or 0), float(fmt.get("tbr") or 0)))
    return str(selected.get("url"))


def preview_media(url: str) -> dict[str, Any]:
    valid, reason = validate_public_url(url)
    if not valid:
        raise ValueError(reason)

    platform = _platform_name(url)
    gallery_error: str | None = None
    if platform in {"TikTok", "Instagram"}:
        image_urls, gallery_error = _gallery_image_urls(url)
        if image_urls:
            slug = _safe_filename(Path(urlparse(url).path.rstrip("/")).name or "Posting foto")
            return {
                "id": slug,
                "title": f"Posting foto {platform}",
                "uploader": "-",
                "duration": "-",
                "thumbnail": image_urls[0],
                "preview_images": image_urls[:12],
                "preview_image_platform": platform,
                "photo_count": len(image_urls),
                "preview_video_url": None,
                "webpage_url": url,
                "extractor": platform,
                "available_heights": [],
                "estimated_best_size": "Tidak tersedia",
                "size_estimates": [],
                "media_type": "Foto/carousel",
                "detected_kind": "photo",
            }

    try:
        info = _extract_ydl_info(url)
    except Exception as exc:
        combined_error = str(exc)
        if gallery_error:
            combined_error = f"{combined_error} | gallery-dl: {gallery_error}"
        return {
            "id": None,
            "title": f"Media {platform}",
            "uploader": "-",
            "duration": "-",
            "thumbnail": None,
            "preview_images": [],
            "photo_count": 0,
            "preview_video_url": None,
            "webpage_url": url,
            "extractor": platform,
            "available_heights": [],
            "estimated_best_size": "Tidak tersedia",
            "size_estimates": [],
            "media_type": "Belum dapat dipastikan",
            "detected_kind": "unknown",
            "preview_error": combined_error,
        }

    formats = [fmt for fmt in info.get("formats", []) if isinstance(fmt, dict)]
    heights = sorted(
        {int(fmt["height"]) for fmt in formats if isinstance(fmt.get("height"), (int, float))},
        reverse=True,
    )
    size_estimates, estimated_best_size = estimate_format_sizes(formats)
    has_video = any(fmt.get("vcodec") not in {None, "none"} for fmt in formats)
    detected_kind = "video" if has_video else "photo"
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Tanpa judul",
        "uploader": info.get("uploader") or info.get("channel") or "-",
        "duration": human_duration(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "preview_images": [info.get("thumbnail")] if info.get("thumbnail") and not has_video else [],
        "photo_count": 1 if info.get("thumbnail") and not has_video else 0,
        "preview_video_url": _best_progressive_preview_url(formats) if has_video else None,
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor_key") or info.get("extractor") or platform,
        "available_heights": heights,
        "estimated_best_size": estimated_best_size,
        "size_estimates": size_estimates,
        "media_type": "Video" if has_video else "Foto",
        "detected_kind": detected_kind,
    }


def detect_media_kind(url: str) -> str:
    return str(preview_media(url).get("detected_kind") or "unknown")


def _height_filter(resolution: int | None) -> str:
    return f"[height<={resolution}]" if resolution else ""


def build_format_selector(settings: DownloadSettings) -> str:
    if settings.output_kind == "audio":
        return "bestaudio/best"
    height = _height_filter(settings.resolution)
    return f"bestvideo*{height}+bestaudio/best{height}"


def _postprocessors(settings: DownloadSettings) -> list[dict[str, Any]]:
    processors: list[dict[str, Any]] = []
    if settings.output_kind == "audio":
        if settings.audio_format == "original":
            return processors
        return [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": settings.audio_bitrate},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
    if settings.output_kind == "video":
        processors.extend(
            [
                {"key": "FFmpegVideoRemuxer", "preferedformat": settings.container},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ]
        )
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
    if settings.clip_duration:
        options["download_ranges"] = download_range_func(None, [(0.0, float(settings.clip_duration))])
        options["force_keyframes_at_cuts"] = True
    if progress_hook:
        options["progress_hooks"] = [progress_hook]
    if postprocessor_hook:
        options["postprocessor_hooks"] = [postprocessor_hook]
    return options


def _collect_output_files(output_dir: Path, started_at: float, media_id: str | None = None) -> list[Path]:
    output_dir = output_dir.expanduser().resolve()
    candidates: list[Path] = []
    if media_id:
        safe_id = re.escape(str(media_id))
        pattern = re.compile(rf"\[{safe_id}\](?:\.[^.]+)+$", re.IGNORECASE)
        for path in output_dir.rglob("*"):
            if path.is_file() and pattern.search(path.name):
                candidates.append(path)
    if not candidates:
        for path in output_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS or ".temp" in path.parts:
                continue
            try:
                if path.stat().st_mtime >= started_at - 1:
                    candidates.append(path)
            except OSError:
                continue
    return sorted(set(candidates), key=lambda item: item.stat().st_mtime, reverse=True)


def _download_av(
    url: str,
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    started_at = time.time()
    options = build_ydl_options(settings, progress_hook, postprocessor_hook, log_callback)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("Unduhan selesai, tetapi informasi file tidak tersedia.")
    files = _collect_output_files(settings.output_dir, started_at, info.get("id"))
    if not files:
        raise RuntimeError("File hasil tidak ditemukan setelah proses download selesai.")
    return info, files


def _best_thumbnail(info: dict[str, Any]) -> dict[str, Any] | None:
    thumbnails = [item for item in info.get("thumbnails", []) if isinstance(item, dict) and item.get("url")]
    if not thumbnails and info.get("thumbnail"):
        thumbnails = [{"url": info["thumbnail"]}]
    if not thumbnails:
        return None
    return max(
        thumbnails,
        key=lambda item: (
            int(item.get("width") or 0) * int(item.get("height") or 0),
            int(item.get("width") or 0) + int(item.get("height") or 0),
            int(item.get("preference") or 0),
        ),
    )


def _extension_from_response(
    url: str, content_type: str | None, default: str = ".jpg", allowed: set[str] | None = None
) -> str:
    allowed = allowed or IMAGE_EXTENSIONS
    extension = Path(urlparse(url).path).suffix.lower()
    if extension in allowed:
        return extension
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed and guessed.lower() in allowed:
            return guessed.lower()
    return default


def _download_url_to_file(
    media_url: str,
    destination_without_suffix: Path,
    referer: str,
    max_filesize: int | None,
    media_kind: str = "image",
    extra_headers: dict[str, str] | None = None,
) -> Path:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
    }
    if extra_headers:
        headers.update(extra_headers)
    request = Request(media_url, headers=headers)
    allowed = VIDEO_EXTENSIONS if media_kind == "video" else IMAGE_EXTENSIONS
    default_ext = ".mp4" if media_kind == "video" else ".jpg"
    with urlopen(request, timeout=45) as response:
        content_length = response.headers.get("Content-Length")
        if max_filesize and content_length and int(content_length) > max_filesize:
            raise RuntimeError("Ukuran file melebihi batas server.")
        extension = _extension_from_response(
            media_url, response.headers.get("Content-Type"), default_ext, allowed
        )
        destination = destination_without_suffix.with_suffix(extension)
        written = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if max_filesize and written > max_filesize:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    raise RuntimeError("Ukuran file melebihi batas server.")
                handle.write(chunk)
    return destination


def _download_best_thumbnail(
    url: str,
    settings: DownloadSettings,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    info = _extract_ydl_info(url)
    thumbnail = _best_thumbnail(info)
    if not thumbnail:
        raise RuntimeError("Foto atau thumbnail tidak tersedia pada URL ini.")
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    title = _safe_filename(str(info.get("title") or "Thumbnail"))
    media_id = _safe_filename(str(info.get("id") or uuid.uuid4().hex[:8]))
    if log_callback:
        log_callback("Mengunduh foto/thumbnail resolusi tertinggi yang tersedia...")
    output = _download_url_to_file(
        str(thumbnail["url"]),
        settings.output_dir / f"{title} [{media_id}]",
        url,
        settings.max_filesize,
    )
    outputs = _resize_photos_hd([output], settings.photo_max_dimension) if settings.quality_mode == "hd" else [output]
    info["_output_kind"] = "photo"
    info["_photo_count"] = len(outputs)
    info["_photo_source"] = f"Thumbnail {_platform_name(url)}"
    info["_photo_archive"] = False
    return info, outputs


TIKTOK_REHYDRATION_MARKER = (
    '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
)


def _tiktok_item_struct(url: str, timeout: int = 30) -> dict[str, Any] | None:
    """Fetch the TikTok post page directly and pull out the same JSON blob
    the official web app hydrates from (itemInfo.itemStruct). We do this
    ourselves instead of relying on gallery-dl because gallery-dl's TikTok
    extractor only ever reads each slideshow image's plain `imageURL` field
    and never looks at the per-image 'Foto Live' motion-clip data that TikTok
    includes for slides that have one."""
    request = Request(url, headers=_preview_headers_for_platform("TikTok"))
    try:
        with urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    start = html.find(TIKTOK_REHYDRATION_MARKER)
    if start == -1:
        return None
    start += len(TIKTOK_REHYDRATION_MARKER)
    end = html.find("</script>", start)
    if end == -1:
        return None
    try:
        data = json.loads(html[start:end])
    except (ValueError, KeyError):
        return None
    scope = data.get("__DEFAULT_SCOPE__") or {}
    video_detail = scope.get("webapp.video-detail") or {}
    item = video_detail.get("itemInfo", {}).get("itemStruct")
    return item if isinstance(item, dict) else None


def _tiktok_best_video_url(video_obj: dict[str, Any]) -> str | None:
    """Pick the highest-quality playable URL from a TikTok `video` object,
    same approach used for TikTok's main post video (bitrateInfo first,
    falling back to the plain playAddr)."""
    grouped: dict[int, list[str]] = {}
    bitrate_info = video_obj.get("bitrateInfo")
    if bitrate_info:
        if not isinstance(bitrate_info, list):
            bitrate_info = [bitrate_info]
        for entry in bitrate_info:
            if not isinstance(entry, dict):
                continue
            play_addr = entry.get("PlayAddr") or {}
            width = int(play_addr.get("Width") or 0)
            height = int(play_addr.get("Height") or 0)
            url_list = play_addr.get("UrlList") or []
            if url_list:
                grouped.setdefault(width * height, []).extend(url_list)
    ordered: list[str] = []
    for size in sorted(grouped, reverse=True):
        ordered.extend(grouped[size])
    if video_obj.get("playAddr"):
        ordered.append(video_obj["playAddr"])
    return ordered[0] if ordered else None


def _tiktok_slideshow_items(url: str, timeout: int = 30) -> list[dict[str, Any]] | None:
    """Per-image detection for a TikTok photo/slideshow post: each slide is
    inspected on its own, so a carousel with some plain photos and some
    'Foto Live' motion slides mixed together comes back correctly labelled
    instead of collapsing everything into one type. Returns None (meaning
    "could not tell, fall back to gallery-dl") if the page structure doesn't
    match what we expect."""
    try:
        item = _tiktok_item_struct(url, timeout)
    except Exception:
        return None
    if not item or "imagePost" not in item:
        return None
    images = item.get("imagePost", {}).get("images") or []
    if not isinstance(images, list) or not images:
        return None

    results: list[dict[str, Any]] = []
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue
        live_video = image.get("video")
        video_url = _tiktok_best_video_url(live_video) if isinstance(live_video, dict) else None
        if video_url:
            results.append({"type": "video", "url": video_url, "index": index})
            continue
        image_url = None
        image_url_obj = image.get("imageURL")
        if isinstance(image_url_obj, dict):
            url_list = image_url_obj.get("urlList") or []
            image_url = url_list[0] if url_list else None
        if image_url:
            results.append({"type": "image", "url": image_url, "index": index})
    return results or None


def _download_tiktok_slideshow_native(
    url: str,
    output_dir: Path,
    max_filesize: int | None,
    log_callback: Callable[[str], None] | None,
) -> tuple[list[Path], list[Path]] | None:
    """Download a TikTok slideshow using per-image detection: plain slides
    become .jpg, slides that have a 'Foto Live' motion clip become .mp4 —
    automatically, item by item. Returns None if per-item detection wasn't
    possible (caller should fall back to the gallery-dl based path)."""
    items = _tiktok_slideshow_items(url)
    if not items:
        return None

    photo_files: list[Path] = []
    live_video_files: list[Path] = []
    for entry in items:
        index = entry["index"]
        kind = entry["type"]
        media_url = entry["url"]
        if log_callback:
            label = "Foto Live" if kind == "video" else "Foto"
            log_callback(f"Mengunduh {label} {index}/{len(items)}...")
        try:
            if kind == "video":
                output = _download_url_to_file(
                    media_url,
                    output_dir / f"FotoLive_{index:02d}",
                    url,
                    max_filesize,
                    media_kind="video",
                    extra_headers=TIKTOK_IMAGE_HEADERS,
                )
                live_video_files.append(output)
            else:
                output = _download_url_to_file(
                    media_url,
                    output_dir / f"Foto_{index:02d}",
                    url,
                    max_filesize,
                    media_kind="image",
                    extra_headers=TIKTOK_IMAGE_HEADERS,
                )
                photo_files.append(output)
        except Exception as exc:
            if log_callback:
                log_callback(f"Gagal mengunduh item {index}: {exc}")
    if not photo_files and not live_video_files:
        return None
    return photo_files, live_video_files


def _run_gallery_dl(
    url: str,
    output_dir: Path,
    max_filesize: int | None,
    log_callback: Callable[[str], None] | None,
) -> None:
    command = _gallery_common_args() + [
        "--directory",
        str(output_dir),
        "--filename",
        "/O",
    ]
    if max_filesize:
        command.extend(["-o", f"downloader.filesize-max={max_filesize}"])
    command.append(url)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    collected_lines: list[str] = []
    for line in process.stdout:
        clean = line.strip()
        if clean:
            collected_lines.append(clean)
            if log_callback:
                log_callback(clean)
    if process.wait() != 0:
        tail = " | ".join(collected_lines[-5:]) if collected_lines else "tidak ada output."
        raise RuntimeError(f"gallery-dl gagal mengekstrak posting foto. Detail: {tail}"[:700])


def _download_direct_images(
    image_urls: list[str],
    output_dir: Path,
    referer: str,
    max_filesize: int | None,
    log_callback: Callable[[str], None] | None,
) -> list[Path]:
    outputs: list[Path] = []
    for index, media_url in enumerate(image_urls, start=1):
        if log_callback:
            log_callback(f"Mengunduh foto {index} dari {len(image_urls)}...")
        output = _download_url_to_file(
            media_url,
            output_dir / f"Foto_{index:02d}",
            referer,
            max_filesize,
        )
        outputs.append(output)
    return outputs


def _resize_photos_hd(files: list[Path], max_dimension: int = 1920) -> list[Path]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow belum terpasang untuk mode Foto HD.") from exc

    outputs: list[Path] = []
    for path in files:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                width, height = image.size
                if max(width, height) <= max_dimension:
                    outputs.append(path)
                    continue
                scale = max_dimension / max(width, height)
                target = (max(1, round(width * scale)), max(1, round(height * scale)))
                resized = image.resize(target, Image.Resampling.LANCZOS)
                output = path.with_name(f"{path.stem}_HD.jpg")
                if resized.mode not in ("RGB", "L"):
                    resized = resized.convert("RGB")
                resized.save(output, "JPEG", quality=94, optimize=True)
                outputs.append(output)
        except Exception:
            outputs.append(path)
    return outputs


def _archive_files(files: list[Path], destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files, key=lambda item: item.name.lower()):
            archive.write(path, arcname=path.name)
    return destination


def _build_photo_live_slideshow(
    photo_files: list[Path],
    audio_path: Path,
    destination: Path,
    ffmpeg_path: str,
    seconds_per_photo: float = 2.5,
    log_callback: Callable[[str], None] | None = None,
) -> Path:
    """Combine static photos with the post's original background music into a
    single MP4 slideshow — this is the closest faithful reproduction of what
    TikTok's 'Foto Live' (Photo Mode with music) actually looks like when
    played back, since TikTok photo posts have no per-photo motion clip."""
    if not photo_files:
        raise RuntimeError("Tidak ada foto untuk membuat Foto Live.")

    list_file = destination.with_suffix(".txt")
    with list_file.open("w", encoding="utf-8") as handle:
        for photo in photo_files:
            handle.write(f"file '{photo.resolve().as_posix()}'\n")
            handle.write(f"duration {seconds_per_photo}\n")
        # ffmpeg concat demuxer requires the last file repeated without a duration
        handle.write(f"file '{photo_files[-1].resolve().as_posix()}'\n")

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-i",
        str(audio_path),
        "-vf",
        "scale=1080:-2:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    try:
        _run_ffmpeg(command, log_callback)
    finally:
        list_file.unlink(missing_ok=True)
    _ensure_nonempty(destination, "Video Foto Live")
    return destination


def _download_photo_post(
    url: str,
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
    include_music_slideshow: bool = False,
) -> tuple[dict[str, Any], list[Path]]:
    platform = _platform_name(url)
    if platform == "YouTube":
        return _download_best_thumbnail(url, settings, log_callback)

    output_dir = settings.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    if progress_hook:
        progress_hook({"status": "downloading", "downloaded_bytes": 0})

    files: list[Path] = []
    live_video_files: list[Path] = []
    gallery_error: Exception | None = None
    fallback_error: str | None = None

    native_result = None
    if platform == "TikTok":
        if log_callback:
            log_callback("Mendeteksi jenis tiap foto (foto biasa vs Foto Live)...")
        native_result = _download_tiktok_slideshow_native(
            url, output_dir, settings.max_filesize, log_callback
        )

    if native_result is not None:
        files, live_video_files = native_result
    else:
        if not detect_gallery_dl()[0]:
            raise RuntimeError("gallery-dl belum terpasang. Gunakan requirements.txt versi final.")
        try:
            _run_gallery_dl(url, output_dir, settings.max_filesize, log_callback)
        except Exception as exc:
            gallery_error = exc

        extracted = _collect_output_files(output_dir, started_at)
        files = [path for path in extracted if path.suffix.lower() in IMAGE_EXTENSIONS]
        live_video_files = [path for path in extracted if path.suffix.lower() in VIDEO_EXTENSIONS]

    if not files and not live_video_files:
        image_urls, fallback_error = _gallery_image_urls(url)
        if image_urls:
            files = _download_direct_images(image_urls, output_dir, url, settings.max_filesize, log_callback)
    if not files and not live_video_files:
        detail_parts = []
        if gallery_error:
            detail_parts.append(f"gallery-dl (download): {gallery_error}")
        if fallback_error:
            detail_parts.append(f"gallery-dl (get-urls): {fallback_error}")
        detail = f" Detail: {' | '.join(detail_parts)}" if detail_parts else ""
        raise RuntimeError(
            "Tidak ada foto atau Foto Live yang ditemukan pada posting publik ini. Kemungkinan link "
            f"privat/dihapus, atau TikTok/Instagram mengubah struktur halamannya sehingga gallery-dl gagal mengekstrak.{detail}"
        )

    files = sorted(set(files), key=lambda path: path.name.lower())
    live_video_files = sorted(set(live_video_files), key=lambda path: path.name.lower())
    if settings.quality_mode == "hd":
        files = _resize_photos_hd(files, settings.photo_max_dimension)

    for video_path in live_video_files:
        if settings.max_filesize and video_path.stat().st_size > settings.max_filesize:
            video_path.unlink(missing_ok=True)
    live_video_files = [path for path in live_video_files if path.exists()]

    slug = _safe_filename(Path(urlparse(url).path.rstrip("/")).name or f"foto_{int(time.time())}")
    # Foto individual DAN Foto Live/video asli carousel selalu dipertahankan
    # apa adanya (tidak ada yang dipaksa jadi satu jenis saja).
    output_files = list(files) + list(live_video_files)
    archive_path: Path | None = None
    if settings.photo_archive and len(files) > 1:
        archive_path = output_dir / f"Semua_Foto_{slug}.zip"
        _archive_files(files, archive_path)
        if settings.max_filesize and archive_path.stat().st_size > settings.max_filesize:
            archive_path.unlink(missing_ok=True)
        else:
            output_files.append(archive_path)  # ZIP selalu di akhir, bukan menggantikan JPG/PNG.

    live_slideshow_path: Path | None = None
    live_slideshow_note: str | None = None
    if include_music_slideshow and platform == "TikTok":
        ffmpeg_ok, ffmpeg_path = detect_ffmpeg(settings.ffmpeg_location)
        if not ffmpeg_ok or not ffmpeg_path:
            live_slideshow_note = "FFmpeg tidak tersedia; video Foto Live (foto+musik) tidak dibuat."
        else:
            audio_path = _download_post_audio(url, output_dir)
            if not audio_path:
                live_slideshow_note = (
                    "Musik latar posting ini tidak dapat diunduh (mungkin dilindungi lisensi atau "
                    "posting tidak memakai musik), sehingga hanya foto biasa yang tersedia."
                )
            else:
                try:
                    candidate = output_dir / f"{slug}_FotoLive.mp4"
                    live_slideshow_path = _build_photo_live_slideshow(
                        files,
                        audio_path,
                        candidate,
                        ffmpeg_path,
                        seconds_per_photo=settings.photo_live_seconds_per_photo,
                        log_callback=log_callback,
                    )
                    if settings.max_filesize and live_slideshow_path.stat().st_size > settings.max_filesize:
                        live_slideshow_path.unlink(missing_ok=True)
                        live_slideshow_path = None
                        live_slideshow_note = (
                            "Video Foto Live melebihi batas ukuran server, hanya foto biasa yang disediakan."
                        )
                    else:
                        output_files.append(live_slideshow_path)
                except Exception as exc:
                    live_slideshow_note = f"Gagal membuat video Foto Live: {exc}"
                finally:
                    try:
                        if audio_path.parent.exists() and not any(audio_path.parent.iterdir()):
                            audio_path.parent.rmdir()
                    except OSError:
                        pass

    if progress_hook:
        total = sum(path.stat().st_size for path in output_files if path.exists())
        progress_hook({"status": "finished", "downloaded_bytes": total, "total_bytes": total})

    total_item_count = len(files) + len(live_video_files)
    title_parts = []
    if files:
        title_parts.append(f"{len(files)} foto")
    if live_video_files:
        title_parts.append(f"{len(live_video_files)} Foto Live/video")
    title_detail = " + ".join(title_parts) if title_parts else "0 file"

    info: dict[str, Any] = {
        "id": slug,
        "title": f"Foto {platform} ({title_detail})",
        "extractor": platform,
        "webpage_url": url,
        "_output_kind": "photo",
        "_photo_count": len(files),
        "_photo_source": f"Posting {platform}",
        "_photo_archive": bool(archive_path and archive_path.exists()),
        "_photo_live_slideshow": bool(live_slideshow_path),
        "_photo_live_note": live_slideshow_note,
        "_carousel_live_video_count": len(live_video_files),
        "_carousel_total_count": total_item_count,
    }
    return info, output_files


def _run_ffmpeg(command: list[str], log_callback: Callable[[str], None] | None = None) -> None:
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if process.returncode != 0:
        message = (process.stderr or process.stdout or "FFmpeg gagal memproses media.").strip()
        if log_callback and message:
            log_callback(message[-1600:])
        raise RuntimeError(message[-700:])


def _ensure_nonempty(path: Path, label: str) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError(f"{label} gagal dibuat atau kosong.")


def _download_live_photo(
    url: str,
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    ffmpeg_ok, ffmpeg_path = detect_ffmpeg(settings.ffmpeg_location)
    if not ffmpeg_ok or not ffmpeg_path:
        raise RuntimeError("FFmpeg diperlukan untuk membuat Foto Live.")

    output_dir = settings.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / f".live_source_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    duration = max(1, min(int(settings.live_photo_duration), 15))

    try:
        source_settings = DownloadSettings(
            output_kind="video",
            container="mkv",
            resolution=settings.resolution,
            quality_mode=settings.quality_mode,
            output_dir=temp_dir,
            ffmpeg_location=ffmpeg_path,
            # The source is clipped to a few seconds; do not reject it using the full-video
            # filesize estimate reported by the platform. Final outputs are checked below.
            max_filesize=None,
            clip_duration=float(duration) + 0.75,
        )
        info, source_files = _download_av(
            url,
            source_settings,
            progress_hook=progress_hook,
            postprocessor_hook=postprocessor_hook,
            log_callback=log_callback,
        )
        video_files = [path for path in source_files if path.suffix.lower() in VIDEO_EXTENSIONS]
        if not video_files:
            raise RuntimeError("Tidak ditemukan stream video untuk membuat Foto Live.")
        source = max(video_files, key=lambda path: path.stat().st_size)

        title = _safe_filename(str(info.get("title") or "Foto Live"))
        media_id = _safe_filename(str(info.get("id") or uuid.uuid4().hex[:8]))
        base_name = f"{title} [{media_id}] LivePhoto"
        if postprocessor_hook:
            postprocessor_hook({"status": "started", "postprocessor": "Foto Live"})

        if settings.live_photo_format == "webp":
            webp_path = output_dir / f"{base_name}.webp"
            try:
                _run_ffmpeg(
                    [
                        ffmpeg_path,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-t",
                        str(duration),
                        "-vf",
                        "fps=18,scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-an",
                        "-c:v",
                        "libwebp",
                        "-quality",
                        "86",
                        "-compression_level",
                        "5",
                        "-loop",
                        "0",
                        str(webp_path),
                    ],
                    log_callback,
                )
            except RuntimeError as exc:
                raise RuntimeError(f"FFmpeg tidak dapat membuat WebP animasi: {exc}") from exc
            _ensure_nonempty(webp_path, "WebP animasi")
            output_files = [webp_path]
        else:
            jpg_path = output_dir / f"{base_name}.jpg"
            mov_path = output_dir / f"{base_name}.mov"
            zip_path = output_dir / f"{base_name}.zip"

            _run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-ss",
                    "0.25",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(jpg_path),
                ],
                log_callback,
            )
            _ensure_nonempty(jpg_path, "Gambar JPG")

            primary_command = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(mov_path),
            ]
            try:
                _run_ffmpeg(primary_command, log_callback)
            except RuntimeError:
                fallback_command = [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-t",
                    str(duration),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "2",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(mov_path),
                ]
                _run_ffmpeg(fallback_command, log_callback)
            _ensure_nonempty(mov_path, "Klip MOV")

            output_files = [jpg_path, mov_path]
            if settings.live_photo_archive:
                _archive_files(output_files, zip_path)
                output_files.append(zip_path)

        for path in output_files:
            if settings.max_filesize and path.stat().st_size > settings.max_filesize:
                raise RuntimeError(
                    f"Hasil {path.name} berukuran {human_bytes(path.stat().st_size)}, melebihi batas server."
                )

        if postprocessor_hook:
            postprocessor_hook({"status": "finished", "postprocessor": "Foto Live"})
        info["_output_kind"] = "live_photo"
        info["_live_photo_format"] = settings.live_photo_format
        info["_live_photo_duration"] = duration
        info["_live_photo_archive"] = settings.live_photo_archive
        return info, output_files
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _mark_fallback(info: dict[str, Any], message: str) -> dict[str, Any]:
    info["_fallback_reason"] = message
    return info


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

    if settings.output_kind in {"video", "audio"}:
        return _download_av(url, settings, progress_hook, postprocessor_hook, log_callback)

    detected = detect_media_kind(url)

    if settings.output_kind == "auto_photo_live":
        if detected == "photo":
            info, files = _download_photo_post(
                url, settings, progress_hook, log_callback, include_music_slideshow=True
            )
            note = "Terdeteksi sebagai posting foto; foto individual diunduh"
            note += " beserta video Foto Live (foto+musik)." if info.get("_photo_live_slideshow") else "."
            if info.get("_photo_live_note"):
                note += f" Catatan: {info['_photo_live_note']}"
            return _mark_fallback(info, note), files
        try:
            info, files = _download_live_photo(url, settings, progress_hook, postprocessor_hook, log_callback)
            return _mark_fallback(info, "Terdeteksi sebagai video; dibuat menjadi Foto Live."), files
        except Exception as live_error:
            try:
                info, files = _download_photo_post(url, settings, progress_hook, log_callback)
                return _mark_fallback(info, "Deteksi video gagal; posting ternyata berisi foto statis."), files
            except Exception:
                raise live_error

    if settings.output_kind == "photo":
        if detected == "photo":
            return _download_photo_post(url, settings, progress_hook, log_callback)
        info, files = _download_best_thumbnail(url, settings, log_callback)
        return _mark_fallback(info, "URL berisi video; mode Foto mengambil thumbnail terbaik."), files

    if settings.output_kind == "live_photo":
        if detected == "photo":
            info, files = _download_photo_post(
                url, settings, progress_hook, log_callback, include_music_slideshow=True
            )
            note = (
                "Posting berisi foto statis; aplikasi menyediakan foto individual"
            )
            if info.get("_photo_live_slideshow"):
                note += " serta video Foto Live (foto+musik asli posting)."
            else:
                note += " tanpa memaksakan Foto Live palsu."
                if info.get("_photo_live_note"):
                    note += f" Catatan: {info['_photo_live_note']}"
            return _mark_fallback(info, note), files
        try:
            return _download_live_photo(url, settings, progress_hook, postprocessor_hook, log_callback)
        except Exception as live_error:
            try:
                info, files = _download_photo_post(url, settings, progress_hook, log_callback)
                return _mark_fallback(
                    info,
                    "Foto Live tidak dapat dibuat karena media ternyata berupa posting foto; foto individual tetap disediakan.",
                ), files
            except Exception:
                raise live_error

    raise ValueError(f"Jenis output tidak dikenal: {settings.output_kind}")


def selected_format_summary(info: dict[str, Any]) -> dict[str, str]:
    output_kind = info.get("_output_kind")
    fallback = str(info.get("_fallback_reason") or "-")
    if output_kind == "photo":
        result = {
            "Jenis": "Foto / Carousel",
            "Jumlah foto (.jpg/.png/.webp)": str(info.get("_photo_count") or 0),
            "Sumber": str(info.get("_photo_source") or "-"),
            "ZIP tambahan": "Ya" if info.get("_photo_archive") else "Tidak",
            "Video Foto Live (foto+musik)": "Ya" if info.get("_photo_live_slideshow") else "Tidak",
            "Pemilahan otomatis": fallback,
        }
        live_count = int(info.get("_carousel_live_video_count") or 0)
        if live_count:
            result["Foto Live asli carousel (.mp4/.mov)"] = str(live_count)
        return result
    if output_kind == "live_photo":
        format_label = "JPG + MOV" if info.get("_live_photo_format") == "bundle" else "WebP animasi"
        return {
            "Jenis": "Foto Live",
            "Format": format_label,
            "Durasi gerak": f"{info.get('_live_photo_duration', '-')} detik",
            "ZIP tambahan": "Ya" if info.get("_live_photo_archive") else "Tidak",
            "Pemilahan otomatis": fallback,
        }

    requested = info.get("requested_formats")
    streams = requested if isinstance(requested, list) and requested else [info]
    video = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("vcodec") not in {None, "none"}),
        info,
    )
    audio = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("acodec") not in {None, "none"}),
        info,
    )
    height = video.get("height") if isinstance(video, dict) else None
    width = video.get("width") if isinstance(video, dict) else None
    fps = video.get("fps") if isinstance(video, dict) else None
    resolution = f"{int(width)}×{int(height)}" if width and height else f"{int(height)}p" if height else "-"
    return {
        "Resolusi aktual": resolution,
        "FPS": str(int(fps)) if isinstance(fps, (int, float)) else "-",
        "Codec video": str(video.get("vcodec") or "-") if isinstance(video, dict) else "-",
        "Codec audio": str(audio.get("acodec") or "-") if isinstance(audio, dict) else "-",
        "Format ID": str(info.get("format_id") or "-"),
    }


def summarize_files(paths: Iterable[Path]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for path in paths:
        try:
            size = human_bytes(path.stat().st_size)
        except OSError:
            size = "-"
        result.append({"name": path.name, "size": size, "path": str(path)})
    return result
