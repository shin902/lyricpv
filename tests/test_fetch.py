"""fetch.py の純粋関数 (ネットワーク・外部コマンド非依存) のテスト。"""

import subprocess
import sys
import types

import pytest

import lyricpv.fetch as fetch_mod
from lyricpv.fetch import FetchError, extract_youtube_id, is_url


def test_is_url_accepts_http_and_https():
    assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_url("http://example.com/song.mp3")


def test_is_url_rejects_local_paths_and_filenames():
    assert not is_url("http_test.wav")
    assert not is_url("/path/to/song.wav")
    assert not is_url("song.mp3")


def test_extract_youtube_id_watch_url():
    assert extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_short_url():
    assert extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_shorts_url():
    assert extract_youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_embed_url():
    assert extract_youtube_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_returns_none_for_unmatched_url():
    assert extract_youtube_id("https://example.com/video") is None


def test_ffmpeg_to_master_timeout_raises_fetch_error(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600)

    monkeypatch.setattr(fetch_mod.subprocess, "run", fake_run)
    with pytest.raises(FetchError):
        fetch_mod._ffmpeg_to_master(tmp_path / "in.webm", tmp_path / "out.wav")


def test_probe_duration_ms_timeout_raises_fetch_error(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=60)

    monkeypatch.setattr(fetch_mod.subprocess, "run", fake_run)
    with pytest.raises(FetchError):
        fetch_mod._probe_duration_ms(tmp_path / "master.wav")


def test_fetch_youtube_ignores_stale_source_files(monkeypatch, tmp_path):
    """前回失敗時に残った source.* (例: .aac) を新規ダウンロード分と誤検出しない。"""
    out_dir = tmp_path / "song"
    out_dir.mkdir()
    stale = out_dir / "source.aac"
    stale.write_bytes(b"stale")  # アルファベット順で .m4a より前に来る残骸

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, url, download=True):
            (out_dir / "source.m4a").write_bytes(b"fresh")
            return {"title": "曲名", "artist": "歌手", "id": "abcdefghijk"}

    fake_yt_dlp = types.SimpleNamespace(
        YoutubeDL=FakeYDL, utils=types.SimpleNamespace(DownloadError=Exception)
    )
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)

    converted: list[str] = []
    monkeypatch.setattr(
        fetch_mod,
        "_ffmpeg_to_master",
        lambda src, dst: (converted.append(src.name), dst.write_bytes(b"wav"))[-1],
    )
    monkeypatch.setattr(fetch_mod, "_probe_duration_ms", lambda path: 1234)

    result = fetch_mod.fetch_youtube("https://youtu.be/abcdefghijk", out_dir)

    assert not stale.exists()
    assert converted == ["source.m4a"]
    assert result.source_id == "abcdefghijk"
