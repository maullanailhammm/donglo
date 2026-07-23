from dataclasses import replace
from pathlib import Path
import sys
import types

# Stub yt_dlp for unit tests in environments without project dependencies.
yt_dlp_stub = types.ModuleType("yt_dlp")
yt_dlp_stub.YoutubeDL = object
yt_dlp_utils_stub = types.ModuleType("yt_dlp.utils")
yt_dlp_utils_stub.download_range_func = lambda *args, **kwargs: (lambda info, ydl: [])
sys.modules.setdefault("yt_dlp", yt_dlp_stub)
sys.modules.setdefault("yt_dlp.utils", yt_dlp_utils_stub)

import downloader
from downloader import DownloadSettings


def test_validate_supported_urls():
    assert downloader.validate_public_url("https://www.youtube.com/watch?v=abc")[0]
    assert downloader.validate_public_url("https://www.tiktok.com/@x/video/1")[0]
    assert downloader.validate_public_url("https://www.instagram.com/p/abc/")[0]
    assert not downloader.validate_public_url("https://example.com/video")[0]


def test_parse_urls_deduplicates():
    values = downloader.parse_urls("https://youtu.be/a\nhttps://youtu.be/a\nhttps://youtu.be/b")
    assert values == ["https://youtu.be/a", "https://youtu.be/b"]


def test_format_selector_original_and_hd():
    original = DownloadSettings(output_kind="video", resolution=None)
    hd = replace(original, resolution=1080)
    assert downloader.build_format_selector(original) == "bestvideo*+bestaudio/best"
    assert "height<=1080" in downloader.build_format_selector(hd)


def test_photo_defaults_are_individual_not_zip():
    settings = DownloadSettings(output_kind="photo")
    assert settings.photo_archive is False
    assert settings.photo_max_dimension == 1920


def test_live_bundle_keeps_individual_and_optional_zip():
    settings = DownloadSettings(output_kind="live_photo")
    assert settings.live_photo_format == "bundle"
    assert settings.live_photo_archive is True


def test_summary_photo_contains_auto_note():
    summary = downloader.selected_format_summary(
        {
            "_output_kind": "photo",
            "_photo_count": 3,
            "_photo_source": "Posting TikTok",
            "_photo_archive": True,
            "_fallback_reason": "Terdeteksi sebagai posting foto",
        }
    )
    assert summary["Jumlah foto"] == "3"
    assert summary["ZIP tambahan"] == "Ya"
    assert "posting foto" in summary["Pemilahan otomatis"]


def test_summarize_files(tmp_path: Path):
    image = tmp_path / "foto.jpg"
    image.write_bytes(b"12345")
    rows = downloader.summarize_files([image])
    assert rows[0]["name"] == "foto.jpg"
    assert rows[0]["path"] == str(image)


def test_auto_routes_photo_to_individual_photo_handler(monkeypatch, tmp_path: Path):
    settings = DownloadSettings(output_kind="auto_photo_live", output_dir=tmp_path)
    expected_file = tmp_path / "foto.jpg"
    expected_file.write_bytes(b"x")

    monkeypatch.setattr(downloader, "detect_media_kind", lambda url: "photo")
    monkeypatch.setattr(
        downloader,
        "_download_photo_post",
        lambda *args, **kwargs: ({"title": "Foto", "_output_kind": "photo"}, [expected_file]),
    )
    info, files = downloader.download_media("https://www.instagram.com/p/abc/", settings)
    assert files == [expected_file]
    assert "posting foto" in info["_fallback_reason"]


def test_live_photo_static_post_falls_back_to_photos(monkeypatch, tmp_path: Path):
    settings = DownloadSettings(output_kind="live_photo", output_dir=tmp_path)
    expected_file = tmp_path / "foto.png"
    expected_file.write_bytes(b"x")

    monkeypatch.setattr(downloader, "detect_media_kind", lambda url: "photo")
    monkeypatch.setattr(
        downloader,
        "_download_photo_post",
        lambda *args, **kwargs: ({"title": "Foto", "_output_kind": "photo"}, [expected_file]),
    )
    info, files = downloader.download_media("https://www.tiktok.com/@x/photo/1", settings)
    assert files == [expected_file]
    assert "foto statis" in info["_fallback_reason"]
