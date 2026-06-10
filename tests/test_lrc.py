"""LRC / 逐字 LRC パーサのテスト。"""

from lyricpv.lyrics.lrc import is_synced, is_word_synced, parse_lrc


def test_line_lrc():
    lines = parse_lrc("[00:10.00] 一行目\n[00:14.50] 二行目\n")
    assert len(lines) == 2
    assert lines[0].start_ms == 10_000
    assert lines[1].start_ms == 14_500
    assert lines[0].text == "一行目"
    assert is_synced(lines)
    assert not is_word_synced(lines)


def test_meta_tags_skipped():
    lines = parse_lrc("[ar:歌手]\n[ti:曲名]\n[00:01.00] 歌詞\n")
    assert len(lines) == 1
    assert lines[0].text == "歌詞"


def test_repeated_tags_expand():
    lines = parse_lrc("[00:10.00][00:50.00] サビ\n")
    assert [ln.start_ms for ln in lines] == [10_000, 50_000]
    assert all(ln.text == "サビ" for ln in lines)


def test_enhanced_lrc_word_tags():
    lines = parse_lrc("[00:10.00] <00:10.00> 夜に <00:11.20> 駆ける\n")
    assert is_word_synced(lines)
    words = lines[0].words
    assert [w.text for w in words] == ["夜に", "駆ける"]
    assert words[1].start_ms == 11_200
    assert lines[0].text == "夜に 駆ける"


def test_plain_text_has_no_times():
    lines = parse_lrc("ただの歌詞\n二行目\n")
    assert len(lines) == 2
    assert all(ln.start_ms is None for ln in lines)
    assert not is_synced(lines)


def test_millisecond_precision_variants():
    lines = parse_lrc("[01:02.5] a\n[01:02.50] b\n[01:02.500] c\n")
    assert all(ln.start_ms == 62_500 for ln in lines)


def test_sorted_by_time():
    lines = parse_lrc("[00:20.00] 後\n[00:10.00] 先\n")
    assert [ln.text for ln in lines] == ["先", "後"]
