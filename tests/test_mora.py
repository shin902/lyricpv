"""モーラ分割のテスト。"""

from lyricpv.lyrics.mora import count_mora, is_kana, split_mora


def test_basic_katakana():
    assert split_mora("サクラ") == ["サ", "ク", "ラ"]


def test_youon_merges_with_previous():
    assert split_mora("キャット") == ["キャ", "ッ", "ト"]
    assert split_mora("シュワ") == ["シュ", "ワ"]


def test_long_vowel_and_hatsuon_are_independent():
    assert split_mora("トーン") == ["ト", "ー", "ン"]
    assert count_mora("コーヒー") == 4


def test_hiragana():
    assert split_mora("きょう") == ["きょ", "う"]


def test_non_kana_counts_per_char():
    assert count_mora("ABC") == 3


def test_space_ignored():
    assert split_mora("サ ク") == ["サ", "ク"]


def test_is_kana():
    assert is_kana("ア")
    assert is_kana("あ")
    assert not is_kana("漢")
    assert not is_kana("A")
