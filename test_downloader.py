import unittest
from pathlib import Path

from downloader import DownloadSettings, build_format_selector, parse_urls, validate_public_url


class DownloaderTests(unittest.TestCase):
    def test_supported_urls(self) -> None:
        urls = [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.tiktok.com/@name/video/123",
            "https://www.instagram.com/reel/abc/",
        ]
        self.assertTrue(all(validate_public_url(url)[0] for url in urls))

    def test_rejects_lookalike_domains(self) -> None:
        self.assertFalse(validate_public_url("https://youtube.com.evil.example/watch?v=abc")[0])
        self.assertFalse(validate_public_url("file:///etc/passwd")[0])

    def test_url_deduplication(self) -> None:
        value = "https://youtu.be/a\nhttps://youtu.be/a\nhttps://youtu.be/b"
        self.assertEqual(parse_urls(value), ["https://youtu.be/a", "https://youtu.be/b"])

    def test_video_format_selector(self) -> None:
        settings = DownloadSettings(
            output_kind="video",
            container="mp4",
            resolution=1080,
            output_dir=Path("downloads"),
        )
        selector = build_format_selector(settings)
        self.assertIn("height<=1080", selector)
        self.assertIn("ext=mp4", selector)

    def test_audio_format_selector(self) -> None:
        settings = DownloadSettings(output_kind="audio", output_dir=Path("downloads"))
        self.assertEqual(build_format_selector(settings), "bestaudio/best")


if __name__ == "__main__":
    unittest.main()
