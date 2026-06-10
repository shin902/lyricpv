"""fetch.py の純粋関数 (ネットワーク・外部コマンド非依存) のテスト。"""

from lyricpv.fetch import extract_youtube_id, is_url


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
