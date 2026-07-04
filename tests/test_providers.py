"""lyrics/providers.py の Strategy チェーンのテスト (ネットワーク不使用)。"""

from lyricpv.lyrics.providers import (
    LyricsResult,
    SyncedLyricsProvider,
    UserTextProvider,
    build_provider_chain,
    resolve_lyrics,
)


def test_build_provider_chain_default_order():
    chain = build_provider_chain(None, vocaloid=False)
    assert len(chain) == 1
    assert isinstance(chain[0], SyncedLyricsProvider)
    assert chain[0]._provider_names == ["Lrclib", "NetEase", "Megalobiz", "Genius"]


def test_build_provider_chain_vocaloid_prioritizes_netease():
    chain = build_provider_chain(None, vocaloid=True)
    assert len(chain) == 1
    assert isinstance(chain[0], SyncedLyricsProvider)
    assert chain[0]._provider_names == ["NetEase", "Lrclib", "Megalobiz", "Genius"]


def test_build_provider_chain_prefers_user_text_over_search():
    chain = build_provider_chain("[00:01.00] 歌詞", vocaloid=True)
    assert len(chain) == 1
    assert isinstance(chain[0], UserTextProvider)


def test_resolve_lyrics_returns_first_non_none():
    class FakeProvider:
        name = "fake"

        def fetch(self, title, artist):
            return LyricsResult("[00:00.00] hit", "T2")

    result = resolve_lyrics([FakeProvider()], "title", "artist")
    assert result == LyricsResult("[00:00.00] hit", "T2")


def test_resolve_lyrics_falls_back_through_chain():
    class MissProvider:
        name = "miss"

        def fetch(self, title, artist):
            return None

    class HitProvider:
        name = "hit"

        def fetch(self, title, artist):
            return LyricsResult("lrc-text", "T1")

    result = resolve_lyrics([MissProvider(), HitProvider()], "title", "artist")
    assert result == LyricsResult("lrc-text", "T1")


def test_resolve_lyrics_all_miss_yields_t4():
    class MissProvider:
        name = "miss"

        def fetch(self, title, artist):
            return None

    result = resolve_lyrics([MissProvider(), MissProvider()], "title", "artist")
    assert result == LyricsResult(None, "T4")


def test_user_text_provider_word_synced_is_t1():
    lrc = "[00:01.00] <00:01.00> 夜に <00:02.00> 駆ける\n"
    result = UserTextProvider(lrc).fetch("title", "artist")
    assert result == LyricsResult(lrc, "T1")


def test_user_text_provider_line_synced_is_t2():
    lrc = "[00:01.00] 夜に駆ける\n"
    result = UserTextProvider(lrc).fetch("title", "artist")
    assert result == LyricsResult(lrc, "T2")


def test_user_text_provider_partial_word_sync_is_t2():
    # 1 行目のみ逐字タグがあり 2 行目は行タイミングのみ → align() 側は T2 経路
    lrc = "[00:01.00] <00:01.00> 夜に <00:02.00> 駆ける\n[00:04.00] 君の声が聞こえる\n"
    result = UserTextProvider(lrc).fetch("title", "artist")
    assert result == LyricsResult(lrc, "T2")


def test_user_text_provider_plain_text_is_t3():
    plain = "夜に駆ける\n君の声が聞こえる\n"
    result = UserTextProvider(plain).fetch("title", "artist")
    assert result == LyricsResult(plain, "T3")


def test_synced_lyrics_provider_enhanced_hit_is_t1(monkeypatch):
    import sys
    import types

    calls = []

    def fake_search(term, *, enhanced=False, providers=None):
        calls.append((term, enhanced, providers))
        if enhanced:
            return "[00:00.00] <00:00.00> word"
        raise AssertionError("enhanced 検索がヒットしたら通常検索は呼ばれないはず")

    fake_module = types.SimpleNamespace(search=fake_search)
    monkeypatch.setitem(sys.modules, "syncedlyrics", fake_module)

    provider = SyncedLyricsProvider(["Lrclib"])
    result = provider.fetch("title", "artist")
    assert result == LyricsResult("[00:00.00] <00:00.00> word", "T1")
    assert calls == [("title artist", True, ["Lrclib"])]


def test_synced_lyrics_provider_falls_back_to_plain_search_for_t2(monkeypatch):
    import sys
    import types

    def fake_search(term, *, enhanced=False, providers=None):
        if enhanced:
            return None
        return "[00:00.00] line only"

    fake_module = types.SimpleNamespace(search=fake_search)
    monkeypatch.setitem(sys.modules, "syncedlyrics", fake_module)

    provider = SyncedLyricsProvider(["Lrclib"])
    result = provider.fetch("title", "artist")
    assert result == LyricsResult("[00:00.00] line only", "T2")


def test_synced_lyrics_provider_all_miss_returns_none(monkeypatch):
    import sys
    import types

    def fake_search(term, *, enhanced=False, providers=None):
        return None

    fake_module = types.SimpleNamespace(search=fake_search)
    monkeypatch.setitem(sys.modules, "syncedlyrics", fake_module)

    provider = SyncedLyricsProvider(["Lrclib"])
    assert provider.fetch("title", "artist") is None


def test_synced_lyrics_provider_exception_falls_back_to_none(monkeypatch):
    import sys
    import types

    def fake_search(term, *, enhanced=False, providers=None):
        raise RuntimeError("network error")

    fake_module = types.SimpleNamespace(search=fake_search)
    monkeypatch.setitem(sys.modules, "syncedlyrics", fake_module)

    provider = SyncedLyricsProvider(["Lrclib"])
    assert provider.fetch("title", "artist") is None
