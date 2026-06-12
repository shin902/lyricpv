"""強制アラインメント補正 (refine) のテスト。

whisperx は重い任意依存のためインストールせず、フェイクモジュールを
sys.modules に注入して呼び出し契約と時刻の書き換えロジックを検証する。
"""

import sys
import types

import pytest

from lyricpv.refine import (
    DEFAULT_ALIGN_MODEL,
    AlignedChar,
    RefineError,
    _apply_char_times,
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


class _FakeWhisperx:
    """whisperx の最小フェイク。セグメントのテキストを窓内へ線形配置して返す。"""

    def __init__(self):
        self.align_calls: list[dict] = []
        self.loaded: list[tuple] = []

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
    result = refine_phrases(phrases, tmp_path / "vocals.wav", pad_ms=400)

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
