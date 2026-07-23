import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

# Unit tests below exercise pure helpers and do not access the network.
# Provide a minimal stub when yt-dlp is not installed in the build environment.
try:
    import yt_dlp  # noqa: F401
except ImportError:
    sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=object)

from downloader import (
    DownloadSettings,
    IMAGE_EXTENSIONS,
    _archive_files,
    build_format_selector,
    parse_urls,
    selected_format_summary,
    validate_public_url,
)


class DownloaderTests(unittest.TestCase):
    def test_supported_urls(self) -> None:
        urls = [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.tiktok.com/@name/video/123",
            "https://www.tiktok.com/@name/photo/123",
            "https://www.instagram.com/p/abc/",
            "https://www.instagram.com/reel/abc/",
        ]
        self.assertTrue(all(validate_public_url(url)[0] for url in urls))

    def test_rejects_lookalike_domains(self) -> None:
        self.assertFalse(validate_public_url("https://youtube.com.evil.example/watch?v=abc")[0])
        self.assertFalse(validate_public_url("file:///etc/passwd")[0])

    def test_url_deduplication(self) -> None:
        value = "https://youtu.be/a\nhttps://youtu.be/a\nhttps://youtu.be/b"
        self.assertEqual(parse_urls(value), ["https://youtu.be/a", "https://youtu.be/b"])

    def test_video_format_selector_keeps_quality_first(self) -> None:
        settings = DownloadSettings(
            output_kind="video",
            container="mp4",
            resolution=1080,
            output_dir=Path("downloads"),
        )
        selector = build_format_selector(settings)
        self.assertIn("height<=1080", selector)
        self.assertIn("bestvideo*", selector)
        self.assertNotIn("bestvideo[height<=1080][ext=mp4]", selector)

    def test_audio_format_selector(self) -> None:
        settings = DownloadSettings(output_kind="audio", output_dir=Path("downloads"))
        self.assertEqual(build_format_selector(settings), "bestaudio/best")

    def test_photo_extensions_include_modern_formats(self) -> None:
        self.assertTrue({".jpg", ".webp", ".avif", ".heic"}.issubset(IMAGE_EXTENSIONS))

    def test_photo_summary(self) -> None:
        summary = selected_format_summary(
            {
                "_output_kind": "photo",
                "_photo_count": 4,
                "_photo_source": "Posting TikTok",
                "_photo_archive": True,
            }
        )
        self.assertEqual(summary["Jumlah foto"], "4")
        self.assertEqual(summary["ZIP dibuat"], "Ya")

    def test_archive_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "1.jpg"
            second = root / "2.png"
            first.write_bytes(b"jpg")
            second.write_bytes(b"png")
            archive_path = _archive_files([first, second], root / "photos.zip")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(sorted(archive.namelist()), ["1.jpg", "2.png"])


if __name__ == "__main__":
    unittest.main()
