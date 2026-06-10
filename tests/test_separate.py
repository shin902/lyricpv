"""音源分離 (Demucs / MPS) のテスト。

モデルのダウンロード (数百 MB) と実計算を伴うため、既定では skip し、
``LYRICPV_RUN_SEPARATION_TESTS=1`` のときだけ実行する。
"""

import os

import pytest

from lyricpv.device import pick_device

requires_separation = pytest.mark.skipif(
    os.environ.get("LYRICPV_RUN_SEPARATION_TESTS") != "1",
    reason="LYRICPV_RUN_SEPARATION_TESTS=1 のときのみ実行 (モデルDLと実計算を伴う)",
)


def test_pick_device_returns_valid_device():
    dev = pick_device()
    assert dev.type in ("mps", "cuda", "cpu")


def test_pick_device_cpu_forced():
    assert pick_device("cpu").type == "cpu"


@requires_separation
def test_separation_produces_stems(synth_wav_path, tmp_path):
    from lyricpv.separate import separate

    result = separate(synth_wav_path, tmp_path)
    assert result.vocals_path.exists()
    assert result.accompaniment_path.exists()
    assert result.device_used in ("mps", "cuda", "cpu")

    import soundfile as sf

    vocals, sr = sf.read(result.vocals_path)
    assert sr == result.sample_rate
    assert len(vocals) > 0
