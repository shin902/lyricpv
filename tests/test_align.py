"""モーラ按分アライメントのテスト。"""

from unittest.mock import patch

from lyricpv.lyrics.align import (
    _MAX_MS_PER_MORA,
    _TYPICAL_MS_PER_MORA,
    _active_regions,
    _align_plain,
    _vocal_active_span,
    align,
)
from lyricpv.lyrics.lrc import LyricLine, parse_lrc
from lyricpv.lyrics.morph import MorphWord, analyze_line
from lyricpv.schema import AmplitudePoint


def _assert_hierarchy_consistent(phrases):
    """phrase→word→char の時刻が単調かつ親区間に収まることを確認する。"""
    for p in phrases:
        assert p.start_time <= p.end_time
        prev_end = p.start_time
        for w in p.words:
            assert p.start_time <= w.start_time <= w.end_time <= p.end_time
            assert w.start_time >= prev_end - 1  # 按分の丸めで 1ms の重なりは許容
            prev_end = w.end_time
            c_prev = w.start_time
            for c in w.chars:
                assert w.start_time <= c.start_time <= c.end_time <= w.end_time
                assert c.start_time >= c_prev - 1
                c_prev = c.end_time


def test_line_synced_distribution():
    lines = parse_lrc("[00:10.00] 夜に駆ける\n[00:14.00] 君の声\n")
    phrases = align(lines, 200_000)
    assert len(phrases) == 2
    assert phrases[0].start_time == 10_000
    # 次の行の開始までに行が終わる
    assert phrases[0].end_time <= 14_000
    _assert_hierarchy_consistent(phrases)
    # 全文字が word に分配されている
    text = "".join(c.char for w in phrases[0].words for c in w.chars)
    assert text == "夜に駆ける"


def test_long_gap_is_capped():
    # 行間が 60 秒空いても、行の長さはモーラ数上限で切り上げられる
    lines = parse_lrc("[00:10.00] あい\n[01:10.00] うえ\n")
    phrases = align(lines, 200_000)
    assert phrases[0].end_time < 20_000


def test_word_synced_uses_given_times():
    lines = parse_lrc("[00:10.00] <00:10.00> 夜に <00:12.00> 駆ける\n")
    phrases = align(lines, 200_000)
    words = phrases[0].words
    assert words[0].start_time == 10_000
    assert words[0].end_time == 12_000
    assert words[1].start_time == 12_000
    _assert_hierarchy_consistent(phrases)


def test_plain_text_uses_amplitude_span():
    lines = parse_lrc("一行目の歌詞\n二行目の歌詞\n")
    amplitude = [
        AmplitudePoint(time=t, value=0.0 if t < 20_000 else 0.8) for t in range(0, 100_000, 1000)
    ]
    phrases = align(lines, 100_000, amplitude)
    assert len(phrases) == 2
    # 歌唱開始 (20 秒) より前に歌詞が置かれない
    assert phrases[0].start_time >= 20_000
    _assert_hierarchy_consistent(phrases)


def test_plain_text_skips_interlude_between_active_regions():
    """有声区間が 2 つに割れている場合、行は間奏を避けて配置される (#3)。"""
    lines = parse_lrc("一行目の歌詞\n二行目の歌詞\n")
    # 10–30 秒と 60–80 秒だけ有声、その間は間奏 (無音)
    amplitude = [
        AmplitudePoint(time=t, value=0.8 if 10_000 <= t < 30_000 or 60_000 <= t < 80_000 else 0.0)
        for t in range(0, 100_000, 500)
    ]
    phrases = align(lines, 100_000, amplitude)
    assert len(phrases) == 2
    # モーラ数が同じ 2 行なので、1 行目は前半区間、2 行目は後半区間に収まる
    assert 10_000 <= phrases[0].start_time < 30_500
    assert 60_000 <= phrases[1].start_time < 80_500
    _assert_hierarchy_consistent(phrases)


def test_active_regions_merges_short_gaps_and_drops_blips():
    duration = 100_000
    points = (
        # 息継ぎ程度 (1 秒) のギャップを挟む本体区間 → 1 区間にマージされる
        [AmplitudePoint(time=t, value=0.8) for t in range(10_000, 15_000, 500)]
        + [AmplitudePoint(time=t, value=0.8) for t in range(16_000, 20_000, 500)]
        # 500ms だけの孤立した断片 → ノイズとして捨てられる
        + [AmplitudePoint(time=t, value=0.8) for t in range(50_000, 50_500, 100)]
    )
    regions = _active_regions(duration, points)
    assert len(regions) == 1
    start, end = regions[0]
    assert start == 10_000
    assert 19_500 <= end <= 20_500


def test_active_regions_falls_back_to_span_without_amplitude():
    duration = 100_000
    assert _active_regions(duration, None) == [_vocal_active_span(duration, None)]


def test_empty_lines_return_empty():
    assert align([], 100_000) == []
    assert align(parse_lrc("\n\n"), 100_000) == []


def test_small_kana_gets_shorter_span():
    lines = parse_lrc("[00:00.00] キャット\n[00:04.00] 次\n")
    phrases = align(lines, 10_000)
    chars = [c for w in phrases[0].words for c in w.chars]
    by_char = {c.char: c.end_time - c.start_time for c in chars}
    # 小書き「ャ」は通常文字より短い
    assert by_char["ャ"] < by_char["キ"]


def test_align_plain_skips_empty_morph_lines_without_gap():
    # 形態素解析結果が空になる行 (記号のみ等) は按分対象から除外され、
    # その行の分の weight が total に混入して隙間が残ることを防ぐ
    word_a = MorphWord(surface="a", pos="名詞", reading="a", mora_count=1)
    word_b = MorphWord(surface="b", pos="名詞", reading="b", mora_count=1)
    morphs_by_text = {"a": [word_a], "skip": [], "b": [word_b]}

    lines = [LyricLine(text="a"), LyricLine(text="skip"), LyricLine(text="b")]
    duration_ms = 100_000
    with patch("lyricpv.lyrics.align.analyze_line", side_effect=morphs_by_text.__getitem__):
        phrases = _align_plain(lines, duration_ms, None)

    assert len(phrases) == 2
    span_start, span_end = _vocal_active_span(duration_ms, None)
    span = span_end - span_start
    # "skip" 行の weight が total に混入していれば start_time はもっと小さくなる
    assert phrases[1].start_time == span_start + span // 2


def test_word_synced_last_word_uses_typical_mora_duration():
    # 最終単語に次の時刻が無い場合の長さ推定は、上限値 (500ms/モーラ) で
    # 引き伸ばすのではなく典型値 (180ms/モーラ) を使う
    lines = parse_lrc("[00:10.00] <00:10.00> ありがとうございます\n")
    phrases = align(lines, 200_000)
    last_word = phrases[0].words[-1]
    duration = last_word.end_time - last_word.start_time

    moras = sum(m.mora_count for m in analyze_line(last_word.text))
    assert duration == moras * _TYPICAL_MS_PER_MORA
    assert duration < moras * _MAX_MS_PER_MORA
