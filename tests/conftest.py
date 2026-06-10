"""テスト用の合成音源フィクスチャ。

ネットワークも GPU も使わず、解析アルゴリズムの振る舞いだけを検証するため、
クリック (120BPM) + Cメジャー和音の合成 WAV を生成する。
"""

import numpy as np
import pytest
import soundfile as sf

SR = 22_050


def synth_song(duration_s: float = 12.0, bpm: float = 120.0) -> np.ndarray:
    """120BPM のクリックと C メジャー和音 (C4/E4/G4) を重ねた合成音。"""
    n = int(SR * duration_s)
    t = np.arange(n) / SR

    # C メジャー和音 (倍音を少し足して現実の音に近づける)
    chord = np.zeros(n)
    for f in (261.63, 329.63, 392.00):
        chord += 0.2 * np.sin(2 * np.pi * f * t) + 0.05 * np.sin(2 * np.pi * 2 * f * t)

    # クリック: 拍頭に 30ms の減衰ノイズバースト
    rng = np.random.default_rng(0)
    click = np.zeros(n)
    interval = int(SR * 60 / bpm)
    burst_len = int(SR * 0.03)
    envelope = np.linspace(1.0, 0.0, burst_len)
    for start in range(0, n - burst_len, interval):
        click[start : start + burst_len] += rng.standard_normal(burst_len) * envelope

    mix = chord + 0.8 * click
    return (mix / np.abs(mix).max() * 0.9).astype(np.float32)


@pytest.fixture(scope="session")
def synth_wav_path(tmp_path_factory):
    """合成曲のモノラル WAV ファイル (セッション内で再利用)。"""
    path = tmp_path_factory.mktemp("audio") / "synth.wav"
    sf.write(path, synth_song(), SR)
    return path
