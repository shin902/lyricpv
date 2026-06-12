"""強制アラインメント補正 (refine) のテスト。

whisperx は重い任意依存のためインストールせず、フェイクモジュールを
sys.modules に注入して呼び出し契約と時刻の書き換えロジックを検証する。
"""

import sys
import types

import pytest

from lyricpv.refine import (
    DEFAULT_ALIGN_MODEL,
    _MAX_LAST_CHAR_MS,
    _TYPICAL_CHAR_MS,
    AlignedChar,
    RefineError,
    RefineParams,
    _apply_char_times,
    _clamp_tail,
    refine_phrases,
)
from lyricpv.schema import Char, Phrase, Word


def make_phrase(word_surfaces: list[str], start: int, end: int) -> Phrase:
    """文字を均等割りした按分相当のフレーズを組み立てる。"""
    n_chars = sum(len(s) for s in word_surfaces)
    step = (end - start) / n_chars
    cursor = float(start)
    words = []
    for surface in word_surfaces:
        chars = []
        for ch in surface:
            c0, c1 = int(cursor), int(cursor + step)
            chars.append(Char(start_time=c0, end_time=max(c1, c0 + 1), char=ch))
            cursor += step
        words.append(
            Word(
                start_time=chars[0].start_time,
                end_time=chars[-1].end_time,
                text=surface,
                pos="名詞",
                chars=chars,
            )
        )
    return Phrase(start_time=start, end_time=end, text="".join(word_surfaces), words=words)


def flat_chars(phrase: Phrase) -> list[Char]:
    return [c for w in phrase.words for c in w.chars]


def test_apply_char_times_overwrites_with_measured_values():
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    aligned = [
        AlignedChar("夜", 10_100, 10_300),
        AlignedChar("に", 10_400, 10_600),
        AlignedChar("駆", 10_800, 11_000),
        AlignedChar("け", 11_000, 11_200),
        AlignedChar("る", 11_300, 11_600),
    ]
    assert _apply_char_times(p, aligned)

    chars = flat_chars(p)
    assert chars[0].start_time == 10_100
    assert chars[4].end_time == 11_600
    # word / phrase の時刻も char から再構成される
    assert p.words[2].start_time == 10_800
    assert p.words[2].end_time == 11_600
    assert p.start_time == 10_100
    assert p.end_time == 11_600


def test_apply_char_times_interpolates_unrecognized_chars():
    # 「駆」だけ実測が付かない (漢字の認識漏れを想定) → 前後の確定点の間に置かれる
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    aligned = [
        AlignedChar("夜", 10_100, 10_300),
        AlignedChar("に", 10_400, 10_600),
        AlignedChar("け", 11_000, 11_200),
        AlignedChar("る", 11_300, 11_600),
    ]
    assert _apply_char_times(p, aligned)

    chars = flat_chars(p)
    kakeru = chars[2]
    assert kakeru.char == "駆"
    assert 10_600 <= kakeru.start_time <= kakeru.end_time <= 11_000
    # 中抜けがあっても後続の「け」「る」は実測値を保つ
    assert chars[3].start_time == 11_000
    assert chars[4].start_time == 11_300


def test_apply_char_times_rejects_low_match_ratio():
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    before = [(c.start_time, c.end_time) for c in flat_chars(p)]
    aligned = [AlignedChar("あ", 10_100, 10_200), AlignedChar("い", 10_300, 10_400)]

    assert not _apply_char_times(p, aligned)
    assert [(c.start_time, c.end_time) for c in flat_chars(p)] == before
    assert p.start_time == 10_000  # フレーズ時刻も按分値のまま


def test_apply_char_times_rejects_start_before_previous_phrase():
    p = make_phrase(["夜"], 10_000, 11_000)
    aligned = [AlignedChar("夜", 9_000, 9_500)]
    assert not _apply_char_times(p, aligned, min_start=9_500)


def test_apply_char_times_rejects_window_edge_saturation():
    """実測が探索窓の先頭に張り付いた行は縮退アラインメントとみなして棄却する。
    (実データで -pad ちょうどに張り付く行が観測された #3)"""
    p = make_phrase(["夜", "に"], 10_000, 12_000)
    window_start = 9_600
    aligned = [AlignedChar("夜", 9_610, 9_800), AlignedChar("に", 9_900, 10_100)]
    assert not _apply_char_times(p, aligned, window_start=window_start)
    # 窓の端から十分離れていれば採用される
    aligned_ok = [AlignedChar("夜", 10_200, 10_400), AlignedChar("に", 10_500, 10_700)]
    assert _apply_char_times(p, aligned_ok, window_start=window_start)


def test_apply_char_times_does_not_stretch_trailing_punctuation():
    """行末の実測なし文字 (括弧・リーダー等) は最後の確定点の直後に短く置き、
    行末まで引き伸ばさない (実データで最大 4 秒のギャップが観測された #3)。"""
    p = make_phrase(["はい", "）"], 10_000, 16_000)
    aligned = [AlignedChar("は", 10_100, 10_300), AlignedChar("い", 10_400, 10_600)]
    assert _apply_char_times(p, aligned)

    paren = flat_chars(p)[-1]
    assert paren.char == "）"
    assert paren.start_time == 10_600
    assert paren.end_time <= 10_600 + _TYPICAL_CHAR_MS
    assert p.end_time == paren.end_time  # フレーズ末尾も追従して縮む


def test_apply_char_times_ignores_measured_times_for_punctuation():
    """句読点・記号は whisperx が補間時刻 (窓の末尾など) を返しても採らず、
    直前の確定点に隣接させる。"""
    p = make_phrase(["はい", "）"], 10_000, 16_000)
    aligned = [
        AlignedChar("は", 10_100, 10_300),
        AlignedChar("い", 10_400, 10_600),
        AlignedChar("）", 15_900, 15_950),  # whisperx の補間値 (信用しない)
    ]
    assert _apply_char_times(p, aligned)
    paren = flat_chars(p)[-1]
    assert paren.char == "）"
    assert paren.start_time == 10_600


def test_apply_char_times_caps_silence_absorbed_last_char():
    """行末の無音・間奏を吸収して伸びた最後の文字は上限で切り詰める
    (実データで 4.3 秒に伸びた「ど」を観測 #3)。"""
    p = make_phrase(["はい"], 10_000, 16_000)
    aligned = [AlignedChar("は", 10_100, 10_300), AlignedChar("い", 10_400, 14_700)]
    assert _apply_char_times(p, aligned)
    last = flat_chars(p)[-1]
    assert last.end_time == 10_400 + _MAX_LAST_CHAR_MS
    assert p.end_time == last.end_time


def test_apply_char_times_ignores_squashed_final_char():
    """弱く歌われた行末の文字 (「〜だろう」の「う」等) は 1 フレームに潰れた
    実測 (score~0) で返るため採らず、直前の確定点の直後に置く (#3)。"""
    p = make_phrase(["だろう"], 10_000, 14_000)
    aligned = [
        AlignedChar("だ", 10_100, 10_400, score=0.9),
        AlignedChar("ろ", 10_400, 11_000, score=0.9),
        AlignedChar("う", 11_000, 11_020, score=0.0),  # 1 フレームの潰れ
    ]
    assert _apply_char_times(p, aligned)
    u = flat_chars(p)[-1]
    assert u.char == "う"
    assert u.start_time == 11_000  # 「ろ」の実測終了に隣接
    assert u.end_time <= 11_000 + _TYPICAL_CHAR_MS


def test_apply_char_times_ignores_low_score_chars():
    """スコアの低い実測 (CTC の自信がない文字) は採らず補間に回す。"""
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    aligned = [
        AlignedChar("夜", 10_100, 10_300, score=0.9),
        AlignedChar("に", 10_350, 10_500, score=0.1),  # 低スコアの潰れ
        AlignedChar("駆", 10_800, 11_000, score=0.9),
        AlignedChar("け", 11_000, 11_200, score=0.9),
        AlignedChar("る", 11_300, 11_600, score=0.9),
    ]
    assert _apply_char_times(p, aligned)
    ni = flat_chars(p)[1]
    assert ni.char == "に"
    # 実測 (10_350) ではなく前後の確定点の間に補間される
    assert 10_300 <= ni.start_time <= ni.end_time <= 10_800


def test_apply_char_times_rejects_collapsed_path():
    """行中間に潰れ実測が複数ある行は CTC パスの崩壊とみなして棄却する
    (実データで文字が数十 ms 間隔で流れる行を観測 #3)。"""
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    before = [(c.start_time, c.end_time) for c in flat_chars(p)]
    aligned = [
        AlignedChar("夜", 10_100, 10_300, score=0.9),
        AlignedChar("に", 10_300, 10_320, score=0.0),  # 潰れ (中間)
        AlignedChar("駆", 10_320, 10_340, score=0.0),  # 潰れ (中間)
        AlignedChar("け", 10_340, 10_400, score=0.66),
        AlignedChar("る", 10_400, 10_700, score=0.9),
    ]
    assert not _apply_char_times(p, aligned)
    assert [(c.start_time, c.end_time) for c in flat_chars(p)] == before


def test_apply_char_times_rejects_heavy_crossing_into_next_phrase():
    """次行の頭を追い越す文字が多い行 (コール&レスポンスの応答が次行に
    重なって歌われる行) は、切り詰めると後半が表示されなくなるため棄却する。"""
    p = make_phrase(["はい", "そう"], 10_000, 12_000)
    before = [(c.start_time, c.end_time) for c in flat_chars(p)]
    aligned = [
        AlignedChar("は", 10_100, 10_300, score=0.9),
        AlignedChar("い", 10_400, 10_600, score=0.9),
        AlignedChar("そ", 12_300, 12_500, score=0.9),  # 次行 (12_000) を追い越し
        AlignedChar("う", 12_500, 12_700, score=0.9),  # 同上
    ]
    assert not _apply_char_times(p, aligned, next_start=12_000)
    assert [(c.start_time, c.end_time) for c in flat_chars(p)] == before

    # 追い越しが許容数 (既定 1 文字) 以内なら採用される
    aligned_ok = [
        AlignedChar("は", 10_100, 10_300, score=0.9),
        AlignedChar("い", 10_400, 10_600, score=0.9),
        AlignedChar("そ", 11_300, 11_500, score=0.9),
        AlignedChar("う", 12_300, 12_500, score=0.9),  # 1 文字だけ追い越し
    ]
    assert _apply_char_times(p, aligned_ok, next_start=12_000)


def test_refine_params_can_loosen_collapse_threshold():
    """max_squashed_mid_chars を緩めると崩壊判定の行も補正対象にできる。"""
    p = make_phrase(["夜", "に", "駆ける"], 10_000, 12_000)
    aligned = [
        AlignedChar("夜", 10_100, 10_300, score=0.9),
        AlignedChar("に", 10_300, 10_320, score=0.0),
        AlignedChar("駆", 10_320, 10_340, score=0.0),
        AlignedChar("け", 10_340, 10_400, score=0.66),
        AlignedChar("る", 10_400, 10_700, score=0.9),
    ]
    params = RefineParams(max_squashed_mid_chars=2)
    assert _apply_char_times(p, aligned, params=params)


def test_refine_params_validation():
    with pytest.raises(ValueError):
        RefineParams(min_char_score=1.5)
    with pytest.raises(ValueError):
        RefineParams(pad_ms=-1)
    with pytest.raises(ValueError):
        RefineParams(model_name="")


def test_clamp_tail_removes_start_time_inversions():
    """前の行の末尾文字が次の行の頭を追い越したら切り詰める (SDK の二分探索対策)。"""
    prev = make_phrase(["はい", "）"], 10_000, 13_000)
    # 「）」は 12_000 から始まる。次の行が 11_500 に始まり追い越されたとする
    _clamp_tail(prev, 11_500)

    last = flat_chars(prev)[-1]
    assert last.start_time == 11_500
    assert last.end_time >= 11_501
    assert prev.words[-1].start_time == 11_500
    # 切り詰め後も flat 配列は非減少
    starts = [c.start_time for c in flat_chars(prev)]
    assert starts == sorted(starts)


class _FakeWhisperx:
    """whisperx の最小フェイク。セグメントのテキストを窓内へ線形配置して返す。"""

    def __init__(self):
        self.align_calls: list[dict] = []
        self.loaded: list[tuple] = []
        self.span_ratio = 1.0  # 歌唱が窓のどこまでで終わるかを変えるテスト用つまみ

    def load_audio(self, path):
        self.audio_path = path
        return [0.0]  # ダミー波形

    def load_align_model(self, *, language_code, device, model_name=None):
        self.loaded.append((language_code, device, model_name))
        return "fake-model", {"language": language_code}

    def align(self, segments, model, metadata, audio, device, return_char_alignments=False):
        self.align_calls.append(
            {"segments": segments, "return_char_alignments": return_char_alignments}
        )
        out = []
        for seg in segments:
            text = seg["text"]
            margin = 0.05
            start, end = seg["start"] + margin, seg["end"] - margin
            end = start + (end - start) * self.span_ratio
            step = (end - start) / max(1, len(text))
            chars = []
            for i, ch in enumerate(text):
                if ch.isspace():
                    chars.append({"char": ch})  # 空白には時刻が付かない (実物の挙動)
                else:
                    chars.append(
                        {"char": ch, "start": start + i * step, "end": start + (i + 1) * step}
                    )
            out.append({"text": text, "chars": chars})
        return {"segments": out}


@pytest.fixture
def fake_whisperx(monkeypatch):
    fake = _FakeWhisperx()
    mod = types.ModuleType("whisperx")
    mod.load_audio = fake.load_audio
    mod.load_align_model = fake.load_align_model
    mod.align = fake.align
    monkeypatch.setitem(sys.modules, "whisperx", mod)
    return fake


def test_refine_phrases_updates_times_and_reports_counts(fake_whisperx, tmp_path):
    phrases = [
        make_phrase(["夜", "に", "駆ける"], 1_000, 3_000),
        make_phrase(["君", "の", "声"], 4_000, 6_000),
    ]
    result = refine_phrases(phrases, tmp_path / "vocals.wav", params=RefineParams(pad_ms=400))

    assert result.refined_count == 2
    assert result.total == 2
    assert result.model == DEFAULT_ALIGN_MODEL
    # 行窓は ±pad_ms 広げて探索される
    seg0 = fake_whisperx.align_calls[0]["segments"][0]
    assert seg0["start"] == pytest.approx(0.6)
    assert seg0["end"] == pytest.approx(3.4)
    assert fake_whisperx.align_calls[0]["return_char_alignments"] is True
    # 時刻が実測値 (窓内の線形配置) に書き換わっている
    assert phrases[0].start_time != 1_000
    assert phrases[0].start_time >= 600
    # フレーズの開始順は保たれる (契約A の検証条件)
    assert phrases[0].start_time <= phrases[1].start_time


def test_refine_phrases_extends_tail_to_original_end(fake_whisperx, tmp_path):
    """行末は実測の歌い終わりで切らず、補正前の行末 (T2 では次行頭) まで
    余韻として残す (表示が次のフレーズまで消えない #3)。"""
    fake_whisperx.span_ratio = 0.5  # 歌唱が行窓の前半で終わる音源を模す
    p = make_phrase(["夜", "に", "駆ける"], 1_000, 3_000)
    result = refine_phrases([p], tmp_path / "vocals.wav", params=RefineParams(pad_ms=400))

    assert result.refined_count == 1
    assert p.start_time != 1_000  # 開始は実測で補正される
    assert p.end_time == 3_000  # 行末は補正前の値まで伸びる
    assert p.words[-1].end_time == 3_000
    assert flat_chars(p)[-1].end_time == 3_000


def test_refine_phrases_empty_is_noop(fake_whisperx, tmp_path):
    result = refine_phrases([], tmp_path / "vocals.wav")
    assert (result.refined_count, result.total) == (0, 0)
    assert fake_whisperx.align_calls == []


def test_refine_without_dependency_raises_with_install_hint(tmp_path, monkeypatch):
    # whisperx がインストールされた環境でも import 失敗を確実に再現する
    monkeypatch.setitem(sys.modules, "whisperx", None)
    with pytest.raises(RefineError, match="--extra refine"):
        refine_phrases([make_phrase(["夜"], 0, 1_000)], tmp_path / "vocals.wav")


def test_pipeline_records_refine_model_in_meta(fake_whisperx, tmp_path, synth_wav_path, monkeypatch):
    """--refine-align 相当のオプションで meta.json に補正モデルが残る。"""
    import json
    import shutil
    from pathlib import Path

    import lyricpv.pipeline as pipeline_mod
    from lyricpv.pipeline import PipelineOptions, run
    from lyricpv.separate import SeparationResult

    def fake_separate(wav_path, out_dir, *, model_name, device):
        vocals = Path(out_dir) / "vocals.wav"
        acc = Path(out_dir) / "accompaniment.wav"
        shutil.copyfile(wav_path, vocals)
        shutil.copyfile(wav_path, acc)
        return SeparationResult(vocals, acc, 44_100, "cpu")

    monkeypatch.setattr(pipeline_mod, "separate", fake_separate)

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(
            lyrics_text="[00:01.00] 夜に駆ける\n[00:04.00] 君の声\n",
            refine_align=True,
        ),
    )
    meta = json.loads((result.out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["refineModel"] == DEFAULT_ALIGN_MODEL
    assert meta["refinedPhrases"] == 2
    # 再現性のため、補正パラメータも記録される
    assert meta["refineParams"]["padMs"] == 400
    assert meta["refineParams"]["minCharScore"] == 0.35
    # 補正後も契約A の検証を通る JSON が出力されている
    from lyricpv.schema import LyricData

    LyricData.load(result.json_path)


def test_pipeline_refine_skipped_without_vocals(fake_whisperx, tmp_path, synth_wav_path):
    """skip_separation で分離ボーカルが無い場合は補正をスキップする。"""
    import json

    from lyricpv.pipeline import PipelineOptions, run

    result = run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(
            lyrics_text="[00:01.00] 夜に駆ける\n",
            skip_separation=True,
            refine_align=True,
        ),
    )
    meta = json.loads((result.out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["refineModel"] is None
    assert fake_whisperx.align_calls == []
