from __future__ import annotations

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

VIDEO_AUDIO_EXTENSIONS = {
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

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_AUDIO_EXTENSIONS | {".zip"}


@dataclass(frozen=True)
class DownloadSettings:
    output_kind: str  # video, audio, photo, live_photo
    container: str = "mp4"
    resolution: int | None = None
    quality_mode: str = "original"
    audio_format: str = "mp3"  # mp3 or original
    audio_bitrate: str = "320"
    output_dir: Path = Path("downloads")
    ffmpeg_location: str | None = None
    max_filesize: int | None = None
    photo_archive: bool = True
    live_photo_format: str = "bundle"  # bundle or webp
    live_photo_duration: int = 3


class StreamlitLogger:
    """Minimal yt-dlp logger that forwards useful messages to the UI."""

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


def _format_filesize(fmt: dict[str, Any]) -> int | None:
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


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
    """Temukan FFmpeg dari input pengguna, PATH sistem, atau binary portable."""
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


def detect_gallery_dl() -> tuple[bool, str | None]:
    try:
        import gallery_dl  # type: ignore

        return True, str(getattr(gallery_dl, "__version__", "terpasang"))
    except ImportError:
        return False, None


def _height_filter(resolution: int | None) -> str:
    return f"[height<={resolution}]" if resolution else ""


def build_format_selector(settings: DownloadSettings) -> str:
    if settings.output_kind == "audio":
        return "bestaudio/best"

    height = _height_filter(settings.resolution)
    if settings.container == "mp4":
        return (
            f"bestvideo*{height}+bestaudio[ext=m4a]/"
            f"bestvideo*{height}+bestaudio/best{height}"
        )
    return f"bestvideo*{height}+bestaudio/best{height}"


def _postprocessors(settings: DownloadSettings) -> list[dict[str, Any]]:
    processors: list[dict[str, Any]] = []

    if settings.output_kind == "audio":
        if settings.audio_format == "original":
            return processors
        processors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": settings.audio_bitrate,
            }
        )
        processors.append({"key": "FFmpegMetadata", "add_metadata": True})
        return processors

    if settings.output_kind == "video":
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


def _platform_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtube" in host or host == "youtu.be":
        return "YouTube"
    if "tiktok" in host:
        return "TikTok"
    if "instagram" in host:
        return "Instagram"
    return host or "Platform"


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

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        # Photo-only posts are outside yt-dlp's primary scope. They are handled
        # during download by gallery-dl, so preview falls back to a safe summary.
        return {
            "id": None,
            "title": "Posting foto",
            "uploader": "-",
            "duration": "-",
            "thumbnail": None,
            "webpage_url": url,
            "extractor": _platform_name(url),
            "available_heights": [],
            "estimated_best_size": "Tidak tersedia",
            "size_estimates": [],
            "media_type": "Foto/carousel",
        }

    if not isinstance(info, dict):
        raise RuntimeError("Metadata media tidak dapat dibaca.")

    entries = info.get("entries")
    if isinstance(entries, list) and entries:
        first_entry = next((item for item in entries if isinstance(item, dict)), None)
        if first_entry:
            info = {**info, **first_entry}

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
    has_video = any(fmt.get("vcodec") not in {None, "none"} for fmt in formats)

    return {
        "id": info.get("id"),
        "title": info.get("title") or "Tanpa judul",
        "uploader": info.get("uploader") or info.get("channel") or "-",
        "duration": human_duration(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "extractor": info.get("extractor_key") or info.get("extractor") or _platform_name(url),
        "available_heights": heights,
        "estimated_best_size": estimated_best_size,
        "size_estimates": size_estimates,
        "media_type": "Video" if has_video else "Foto/carousel",
    }


def _collect_output_files(output_dir: Path, started_at: float, media_id: str | None = None) -> list[Path]:
    output_dir = output_dir.expanduser().resolve()
    candidates: list[Path] = []

    if media_id:
        safe_id = re.escape(str(media_id))
        id_pattern = re.compile(rf"\[{safe_id}\](?:\.[^.]+)+$", re.IGNORECASE)
        for path in output_dir.rglob("*"):
            if path.is_file() and id_pattern.search(path.name):
                candidates.append(path)

    if not candidates:
        for path in output_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            if ".temp" in path.parts:
                continue
            try:
                if path.stat().st_mtime >= started_at - 2:
                    candidates.append(path)
            except OSError:
                continue

    return sorted(set(candidates), key=lambda item: item.stat().st_mtime, reverse=True)


def _safe_filename(value: str, fallback: str = "media") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:150] or fallback).strip()


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
    return info, files


def _best_thumbnail(info: dict[str, Any]) -> dict[str, Any] | None:
    thumbnails = [item for item in info.get("thumbnails", []) if isinstance(item, dict) and item.get("url")]
    if not thumbnails and info.get("thumbnail"):
        thumbnails = [{"url": info["thumbnail"]}]
    if not thumbnails:
        return None

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        preference = int(item.get("preference") or 0)
        return width * height, width + height, preference

    return max(thumbnails, key=score)


def _download_youtube_thumbnail(
    url: str,
    settings: DownloadSettings,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("Metadata YouTube tidak dapat dibaca.")

    thumbnail = _best_thumbnail(info)
    if not thumbnail:
        raise RuntimeError("Thumbnail tidak tersedia pada URL YouTube ini.")

    thumb_url = str(thumbnail["url"])
    parsed = urlparse(thumb_url)
    extension = Path(parsed.path).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        extension = ".jpg"

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    title = _safe_filename(str(info.get("title") or "YouTube Thumbnail"))
    media_id = _safe_filename(str(info.get("id") or uuid.uuid4().hex[:8]))
    output = settings.output_dir / f"{title} [{media_id}]{extension}"

    request = Request(
        thumb_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.youtube.com/",
        },
    )
    if log_callback:
        log_callback("Mengunduh thumbnail kualitas tertinggi YouTube...")
    with urlopen(request, timeout=30) as response, output.open("wb") as handle:
        content_length = response.headers.get("Content-Length")
        if settings.max_filesize and content_length and int(content_length) > settings.max_filesize:
            raise RuntimeError("Ukuran thumbnail melebihi batas server.")
        shutil.copyfileobj(response, handle)

    if settings.max_filesize and output.stat().st_size > settings.max_filesize:
        output.unlink(missing_ok=True)
        raise RuntimeError("Ukuran thumbnail melebihi batas server.")

    outputs = [output]
    if settings.quality_mode == "hd":
        outputs = _resize_photos_hd(outputs, 1920)
    info["_output_kind"] = "photo"
    info["_photo_count"] = 1
    info["_photo_source"] = "Thumbnail YouTube"
    info["_quality_mode"] = settings.quality_mode
    return info, outputs


def _run_gallery_dl(
    url: str,
    output_dir: Path,
    max_filesize: int | None,
    log_callback: Callable[[str], None] | None,
) -> None:
    image_filter = "extension.lower() in ('jpg','jpeg','png','webp','avif','heic','heif','gif')"
    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "--no-colors",
        "--windows-filenames",
        "--directory",
        str(output_dir),
        "--filename",
        "/O",
        "--filter",
        image_filter,
        "-o",
        "extractor.tiktok.photos=true",
        "-o",
        "extractor.tiktok.audio=false",
        "-o",
        "extractor.tiktok.covers=false",
        "-o",
        "extractor.instagram.videos=false",
        "-o",
        "extractor.instagram.warn-images=true",
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
    for line in process.stdout:
        clean = line.strip()
        if clean and log_callback:
            log_callback(clean)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            "Foto tidak dapat diekstrak. Pastikan posting bersifat publik dan gallery-dl terbaru sudah terpasang."
        )


def _resize_photos_hd(files: list[Path], max_dimension: int = 1920) -> list[Path]:
    """Create HD copies capped at max_dimension while preserving originals when already smaller."""
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
                out = path.with_name(f"{path.stem}_HD.jpg")
                if resized.mode not in ("RGB", "L"):
                    resized = resized.convert("RGB")
                resized.save(out, "JPEG", quality=92, optimize=True)
                outputs.append(out)
        except Exception:
            outputs.append(path)
    return outputs


def _archive_files(files: list[Path], destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files, key=lambda item: item.name.lower()):
            archive.write(path, arcname=path.name)
    return destination


def _download_photo_post(
    url: str,
    settings: DownloadSettings,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[Path]]:
    platform = _platform_name(url)
    if platform == "YouTube":
        return _download_youtube_thumbnail(url, settings, log_callback)

    gallery_ok, _ = detect_gallery_dl()
    if not gallery_ok:
        raise RuntimeError("gallery-dl belum terpasang. Pastikan requirements.txt versi final digunakan.")

    output_dir = settings.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    if progress_hook:
        progress_hook({"status": "downloading", "downloaded_bytes": 0})
    _run_gallery_dl(url, output_dir, settings.max_filesize, log_callback)
    files = [path for path in _collect_output_files(output_dir, started_at) if path.suffix.lower() in IMAGE_EXTENSIONS]
    if not files:
        raise RuntimeError(
            "Tidak ada foto yang ditemukan. Gunakan URL posting/carousel publik TikTok atau Instagram."
        )

    if settings.quality_mode == "hd":
        files = _resize_photos_hd(files, 1920)

    slug = _safe_filename(Path(urlparse(url).path.rstrip("/")).name or f"foto_{int(time.time())}")
    output_files = list(files)
    archive_path: Path | None = None
    if settings.photo_archive:
        archive_path = output_dir / f"Foto_{slug}.zip"
        _archive_files(files, archive_path)
        if settings.max_filesize and archive_path.stat().st_size > settings.max_filesize:
            archive_path.unlink(missing_ok=True)
            if log_callback:
                log_callback("ZIP tidak dibuat karena ukurannya melebihi batas server; foto individual tetap tersedia.")
        else:
            output_files.insert(0, archive_path)

    if progress_hook:
        total = sum(path.stat().st_size for path in output_files if path.exists())
        progress_hook({"status": "finished", "downloaded_bytes": total, "total_bytes": total})

    info: dict[str, Any] = {
        "id": slug,
        "title": f"Foto {platform} ({len(files)} file)",
        "extractor": platform,
        "webpage_url": url,
        "_output_kind": "photo",
        "_photo_count": len(files),
        "_photo_source": f"Posting {platform}",
        "_photo_archive": bool(archive_path and archive_path.exists()),
    }
    return info, output_files


def _run_ffmpeg(command: list[str], log_callback: Callable[[str], None] | None = None) -> None:
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if process.returncode != 0:
        message = (process.stderr or process.stdout or "FFmpeg gagal memproses media.").strip()
        if log_callback and message:
            log_callback(message[-1200:])
        raise RuntimeError(f"FFmpeg gagal membuat Foto Live: {message[-500:]}")


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
    temp_dir = output_dir / f".live_photo_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_settings = DownloadSettings(
            output_kind="video",
            container="mkv",
            resolution=settings.resolution,
            quality_mode=settings.quality_mode,
            output_dir=temp_dir,
            ffmpeg_location=ffmpeg_path,
            max_filesize=settings.max_filesize,
        )
        info, source_files = _download_av(
            url,
            source_settings,
            progress_hook=progress_hook,
            postprocessor_hook=postprocessor_hook,
            log_callback=log_callback,
        )
        video_files = [path for path in source_files if path.suffix.lower() in VIDEO_AUDIO_EXTENSIONS and path.suffix.lower() not in {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac"}]
        if not video_files:
            raise RuntimeError("Foto Live membutuhkan URL video/Reel/TikTok video, bukan posting foto statis.")
        source = max(video_files, key=lambda path: path.stat().st_size)

        title = _safe_filename(str(info.get("title") or "Foto Live"))
        media_id = _safe_filename(str(info.get("id") or uuid.uuid4().hex[:8]))
        base_name = f"{title} [{media_id}] LivePhoto"
        duration = max(1, min(int(settings.live_photo_duration), 15))

        if postprocessor_hook:
            postprocessor_hook({"status": "started", "postprocessor": "Foto Live"})

        if settings.live_photo_format == "webp":
            webp_path = output_dir / f"{base_name}.webp"
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
                    "fps=20",
                    "-an",
                    "-c:v",
                    "libwebp_anim",
                    "-quality",
                    "85",
                    "-compression_level",
                    "6",
                    "-loop",
                    "0",
                    str(webp_path),
                ],
                log_callback,
            )
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
                    "-ss",
                    str(duration / 2),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(jpg_path),
                ],
                log_callback,
            )
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
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
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
                    str(mov_path),
                ],
                log_callback,
            )
            _archive_files([jpg_path, mov_path], zip_path)
            output_files = [zip_path, jpg_path, mov_path]

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
        return info, output_files
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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

    if settings.output_kind == "photo":
        return _download_photo_post(url, settings, progress_hook, log_callback)
    if settings.output_kind == "live_photo":
        return _download_live_photo(url, settings, progress_hook, postprocessor_hook, log_callback)
    return _download_av(url, settings, progress_hook, postprocessor_hook, log_callback)


def selected_format_summary(info: dict[str, Any]) -> dict[str, str]:
    output_kind = info.get("_output_kind")
    if output_kind == "photo":
        return {
            "Jenis": "Foto",
            "Jumlah foto": str(info.get("_photo_count") or 0),
            "Sumber": str(info.get("_photo_source") or "-"),
            "ZIP dibuat": "Ya" if info.get("_photo_archive") else "Tidak",
        }
    if output_kind == "live_photo":
        format_label = "JPG + MOV dalam ZIP" if info.get("_live_photo_format") == "bundle" else "WebP animasi"
        return {
            "Jenis": "Foto Live",
            "Format": format_label,
            "Durasi gerak": f"{info.get('_live_photo_duration', '-')} detik",
            "Catatan": "Dibuat dari stream video terbaik",
        }

    requested = info.get("requested_formats")
    streams = requested if isinstance(requested, list) and requested else [info]

    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("vcodec") not in {None, "none"}
        ),
        info,
    )
    audio = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("acodec") not in {None, "none"}
        ),
        info,
    )

    height = video.get("height") if isinstance(video, dict) else None
    width = video.get("width") if isinstance(video, dict) else None
    fps = video.get("fps") if isinstance(video, dict) else None

    if width and height:
        resolution = f"{int(width)}×{int(height)}"
    elif height:
        resolution = f"{int(height)}p"
    else:
        resolution = "-"

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
