"""モーラ按分アライメントのテスト。"""

from lyricpv.lyrics.align import _MAX_MS_PER_MORA, _TYPICAL_MS_PER_MORA, align
from lyricpv.lyrics.lrc import parse_lrc
from lyricpv.lyrics.morph import analyze_line
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
    amplitude = [AmplitudePoint(time=t, value=0.0 if t < 20_000 else 0.8) for t in range(0, 100_000, 1000)]
    phrases = align(lines, 100_000, amplitude)
    assert len(phrases) == 2
    # 歌唱開始 (20 秒) より前に歌詞が置かれない
    assert phrases[0].start_time >= 20_000
    _assert_hierarchy_consistent(phrases)


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
