"""ボーカル強化 (enhance) のテスト。

audio-separator は重い任意依存のためインストールせず、フェイクモジュールを
sys.modules に注入して呼び出し契約 (モデル順・ステム選択・後始末) を検証する。
"""

import sys
import types

import pytest

from lyricpv import enhance
from lyricpv.enhance import (
    DEFAULT_DEREVERB_MODEL,
    DEFAULT_KARAOKE_MODEL,
    EnhanceError,
    _pick_stem,
    enhance_vocals,
)


class FakeSeparator:
    """audio_separator.separator.Separator の最小フェイク。

    load_model されたモデルに応じて、ステム名を含むファイルを output_dir に
    生成して相対ファイル名を返す (実物の挙動に合わせる)。
    """

    instances: list["FakeSeparator"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.loaded_models: list[str] = []
        self.separated: list[str] = []
        FakeSeparator.instances.append(self)

    def load_model(self, model_filename):
        self.loaded_models.append(model_filename)

    def separate(self, input_path):
        import shutil
        from pathlib import Path

        out_dir = Path(self.kwargs["output_dir"])
        stem = Path(input_path).stem
        # 実物の命名 ({入力名}_({ステム})_{モデル}.wav) に合わせ、目的でない
        # ステムを先に並べる (順序依存の誤選択をリグレッションとして検出する)
        if "karaoke" in self.loaded_models[-1]:
            names = [f"{stem}_(Instrumental)_km.wav", f"{stem}_(Vocals)_km.wav"]
        else:
            names = [f"{stem}_(Reverb)_dm.wav", f"{stem}_(No Reverb)_dm.wav"]
        for name in names:
            # 中身は入力のコピー (パイプラインテストで librosa が読めるように)
            shutil.copyfile(input_path, out_dir / name)
        self.separated.append(input_path)
        return names


@pytest.fixture
def fake_audio_separator(monkeypatch):
    FakeSeparator.instances = []
    pkg = types.ModuleType("audio_separator")
    mod = types.ModuleType("audio_separator.separator")
    mod.Separator = FakeSeparator
    pkg.separator = mod
    monkeypatch.setitem(sys.modules, "audio_separator", pkg)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", mod)
    return FakeSeparator


def _make_vocals(tmp_path):
    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"RIFF-fake")
    return vocals


def test_enhance_runs_karaoke_then_dereverb(fake_audio_separator, tmp_path):
    result = enhance_vocals(_make_vocals(tmp_path), tmp_path)

    sep = fake_audio_separator.instances[0]
    assert sep.loaded_models == [DEFAULT_KARAOKE_MODEL, DEFAULT_DEREVERB_MODEL]
    # 2 段目の入力は 1 段目の Vocals ステム
    assert "(Vocals)" in sep.separated[1]
    assert result.models_used == [DEFAULT_KARAOKE_MODEL, DEFAULT_DEREVERB_MODEL]
    assert result.vocals_path == tmp_path / enhance.ENHANCED_FILENAME
    assert result.vocals_path.exists()
    # 中間生成物の作業ディレクトリは後始末される
    assert not (tmp_path / "_enhance").exists()


def test_enhance_can_skip_a_stage(fake_audio_separator, tmp_path):
    result = enhance_vocals(_make_vocals(tmp_path), tmp_path, dereverb_model=None)
    sep = fake_audio_separator.instances[0]
    assert sep.loaded_models == [DEFAULT_KARAOKE_MODEL]
    assert result.models_used == [DEFAULT_KARAOKE_MODEL]


def test_enhance_with_no_models_raises(fake_audio_separator, tmp_path):
    with pytest.raises(EnhanceError):
        enhance_vocals(_make_vocals(tmp_path), tmp_path, karaoke_model=None, dereverb_model=None)


def test_enhance_without_dependency_raises_with_install_hint(tmp_path, monkeypatch):
    # audio-separator がインストールされた環境でも import 失敗を確実に再現する
    monkeypatch.setitem(sys.modules, "audio_separator", None)
    with pytest.raises(EnhanceError, match="--extra enhance"):
        enhance_vocals(_make_vocals(tmp_path), tmp_path)


def test_pick_stem_prefers_keyword_then_falls_back(tmp_path):
    picked = _pick_stem(
        ["v_(Instrumental).wav", "v_(Vocals).wav"], ("vocals",), tmp_path, "m"
    )
    assert picked.name == "v_(Vocals).wav"

    # キーワード不一致でも出力が 1 つだけならそれを採用する
    only = _pick_stem(["v_(NoMatch).wav"], ("vocals",), tmp_path, "m")
    assert only.name == "v_(NoMatch).wav"

    # 複数候補から特定できなければ明確なエラー
    with pytest.raises(EnhanceError, match="特定できません"):
        _pick_stem(["a.wav", "b.wav"], ("vocals",), tmp_path, "m")


def test_pick_stem_ignores_input_filename_contamination(tmp_path):
    """入力が vocals.wav のため全出力名に 'vocals' が含まれても、
    括弧ラベルの (Vocals) を持つ方だけを選ぶ (#3 の off-vocal 誤選択)。"""
    outputs = [
        "vocals_(Instrumental)_mel_band_roformer_karaoke.wav",
        "vocals_(Vocals)_mel_band_roformer_karaoke.wav",
    ]
    picked = _pick_stem(outputs, ("vocals",), tmp_path, "m")
    assert "(Vocals)" in picked.name

    # ステム名の空白・大小文字の揺れ ((No Reverb) 等) も正規化して一致させる
    outputs2 = ["x_(Reverb)_dm.wav", "x_(No Reverb)_dm.wav"]
    picked2 = _pick_stem(outputs2, ("noreverb", "dry"), tmp_path, "m")
    assert "(No Reverb)" in picked2.name


def test_pipeline_passes_custom_enhance_models(fake_audio_separator, tmp_path, synth_wav_path, monkeypatch):
    """PipelineOptions のモデル指定 (CLI の --karaoke-model 等) が enhance に届く。"""
    import lyricpv.pipeline as pipeline_mod
    from lyricpv.pipeline import PipelineOptions, run
    from lyricpv.separate import SeparationResult

    def fake_separate(wav_path, out_dir, *, model_name, device):
        import shutil
        from pathlib import Path

        vocals = Path(out_dir) / "vocals.wav"
        acc = Path(out_dir) / "accompaniment.wav"
        shutil.copyfile(wav_path, vocals)
        shutil.copyfile(wav_path, acc)
        return SeparationResult(vocals, acc, 44_100, "cpu")

    monkeypatch.setattr(pipeline_mod, "separate", fake_separate)

    run(
        str(synth_wav_path),
        tmp_path / "out",
        options=PipelineOptions(
            lyrics_text="[00:01.00] 夜に駆ける\n",
            enhance_vocals=True,
            enhance_karaoke_model="my_karaoke_x.ckpt",
            enhance_dereverb_model=None,  # 2 段目スキップ
        ),
    )
    sep = fake_audio_separator.instances[0]
    assert sep.loaded_models == ["my_karaoke_x.ckpt"]


def test_pipeline_records_enhance_models_in_meta(fake_audio_separator, tmp_path, synth_wav_path, monkeypatch):
    """--enhance-vocals 相当のオプションで meta.json に使用モデルが残る。"""
    import json

    import lyricpv.pipeline as pipeline_mod
    from lyricpv.pipeline import PipelineOptions, run

    # 実 Demucs を走らせない: vocals.wav をコピーで作るフェイク分離
    from lyricpv.separate import SeparationResult

    def fake_separate(wav_path, out_dir, *, model_name, device):
        import shutil
        from pathlib import Path

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
            lyrics_text="[00:01.00] 夜に駆ける\n",
            enhance_vocals=True,
        ),
    )
    meta = json.loads((result.out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["enhanceModels"] == [DEFAULT_KARAOKE_MODEL, DEFAULT_DEREVERB_MODEL]
