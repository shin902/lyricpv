"""形態素解析 (fugashi + unidic-lite) のテスト。"""

from lyricpv.lyrics.morph import analyze_line


def test_basic_segmentation():
    words = analyze_line("夜に駆ける")
    surfaces = [w.surface for w in words]
    assert "".join(surfaces) == "夜に駆ける"
    assert len(words) >= 2  # 少なくとも「夜」「に」「駆ける」相当に分かれる


def test_pos_is_japanese_label():
    words = analyze_line("猫が好き")
    noun = next(w for w in words if w.surface == "猫")
    assert noun.pos == "名詞"


def test_mora_count_from_reading():
    words = analyze_line("学校")
    assert words[0].mora_count == 4  # ガッコウ = ガ/ッ/コ/ウ


def test_latin_falls_back_to_char_count():
    words = analyze_line("ABC")
    assert sum(w.mora_count for w in words) >= 1


def test_empty_line():
    assert analyze_line("") == []
