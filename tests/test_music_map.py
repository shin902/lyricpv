"""楽曲地図 (ビート/コード/声量/V-A/構造) の合成音源テスト。"""

import math

import numpy as np
import pytest

from lyricpv import music_map
from lyricpv.schema import LyricData, SongMeta, SongSource


@pytest.fixture(scope="module")
def analyzed(synth_wav_path):
    return music_map.analyze(synth_wav_path)


def test_beats_near_120bpm(analyzed):
    beats = analyzed.beats
    assert len(beats) >= 10
    intervals = np.diff([b.start_time for b in beats])
    # 120BPM = 500ms 間隔 (倍/半テンポ誤検出も許容範囲として 250/1000 を除外しない)
    median = float(np.median(intervals))
    assert median == pytest.approx(500, abs=60) or median == pytest.approx(250, abs=30) or median == pytest.approx(1000, abs=120)


def test_beat_positions_cycle_1_to_4(analyzed):
    positions = [b.position for b in analyzed.beats]
    assert set(positions) <= {1, 2, 3, 4}
    # 連続する拍番号は 4 拍周期で循環する
    for a, b in zip(positions[:-1], positions[1:]):
        assert b == (a % 4) + 1


def test_chords_detect_c_major(analyzed):
    assert analyzed.chords
    # 曲全体が C メジャー和音なので、支配的なコードは C になる
    durations: dict[str, int] = {}
    for c in analyzed.chords:
        durations[c.name] = durations.get(c.name, 0) + (c.end_time - c.start_time)
    dominant = max(durations, key=durations.get)
    assert dominant == "C"


def test_amplitude_normalized_and_sorted(analyzed):
    amp = analyzed.amplitude
    assert amp
    assert all(0.0 <= p.value <= 1.0 for p in amp)
    times = [p.time for p in amp]
    assert times == sorted(times)


def test_valence_arousal_in_range(analyzed):
    va = analyzed.valence_arousal
    assert va
    assert all(-1.0 <= p.valence <= 1.0 and -1.0 <= p.arousal <= 1.0 for p in va)


def test_segments_cover_valid_spans(analyzed):
    segs = analyzed.segments
    assert segs
    for s in segs:
        assert s.start_time < s.end_time
        assert s.label in {"intro", "verse", "chorus", "bridge", "outro"}


def test_amplitude_zero_on_silence(tmp_path):
    import soundfile as sf

    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(22_050 * 3, dtype=np.float32), 22_050)
    mm = music_map.analyze(silent)
    assert all(p.value == 0.0 for p in mm.amplitude)


def test_onset_decay_gate_decays_after_onset():
    onset_env = np.zeros(50)
    onset_env[0] = 10.0  # 先頭のみオンセット
    gate = music_map._onset_decay_gate(onset_env, sr=22_050, hop=music_map.HOP, decay_ms=400.0, floor=0.35)
    assert gate[0] == pytest.approx(1.0)
    assert gate[-1] == pytest.approx(0.35)
    # 新たなオンセットがない区間は単調に減衰する
    assert all(a >= b for a, b in zip(gate, gate[1:]))


def _attack_plus_sustain_signal(sr: int = 22_050, duration_s: float = 3.0) -> np.ndarray:
    """先頭にだけ立ち上がり (オンセット) があり、その後は一定音量で鳴り続ける
    合成音。ハモリ/エコーの余韻を模す。"""
    n = int(sr * duration_s)
    t = np.arange(n) / sr

    rng = np.random.default_rng(0)
    burst_len = int(sr * 0.05)
    attack = np.zeros(n, dtype=np.float64)
    attack[:burst_len] = rng.standard_normal(burst_len)

    sustain = 0.3 * np.sin(2 * np.pi * 440 * t)
    return (attack + sustain).astype(np.float32)


def test_vocal_activity_suppresses_sustained_tail_without_onset():
    """立ち上がりを伴わず鳴り続ける音 (ハモリ/エコーの余韻を想定) は、
    歌唱活動度では立ち上がり直後より下がる (#3)。"""
    sr = 22_050
    _, activity = music_map._vocal_envelopes(_attack_plus_sustain_signal(sr), sr)

    early = next(p for p in activity if p.time < 100)
    late = next(p for p in activity if p.time >= 1500)
    assert late.value < early.value


def test_amplitude_keeps_raw_loudness_unlike_activity():
    """契約A の amplitude は SDK の演出用声量なのでゲートを掛けない。
    余韻区間ではゲート済みの活動度より生の声量が大きいまま残る。"""
    sr = 22_050
    amplitude, activity = music_map._vocal_envelopes(_attack_plus_sustain_signal(sr), sr)

    late_amp = next(p for p in amplitude if p.time >= 1500)
    late_act = next(p for p in activity if p.time >= 1500)
    assert late_act.value < late_amp.value
    # 一定音量で鳴り続けている以上、生の声量は下がらない
    early_amp = next(p for p in amplitude if 200 <= p.time < 300)
    assert late_amp.value == pytest.approx(early_amp.value, abs=0.15)


def test_analyze_returns_vocal_activity(analyzed):
    activity = analyzed.vocal_activity
    assert activity
    assert all(0.0 <= p.value <= 1.0 for p in activity)
    times = [p.time for p in activity]
    assert times == sorted(times)


def test_valence_arousal_finite_on_silence_and_round_trips_json(tmp_path):
    """無音区間でクロマが零ベクトルになっても NaN を出さず JSON 契約が壊れない。"""
    import soundfile as sf

    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(22_050 * 6, dtype=np.float32), 22_050)
    mm = music_map.analyze(silent)

    assert mm.valence_arousal
    for p in mm.valence_arousal:
        assert math.isfinite(p.valence)
        assert math.isfinite(p.arousal)
        assert -1.0 <= p.valence <= 1.0
        assert -1.0 <= p.arousal <= 1.0

    data = LyricData(
        song=SongMeta(title="無音", artist="lyricpv", duration_ms=6000, source=SongSource(type="file", id="silent")),
        valence_arousal=mm.valence_arousal,
    )
    json_path = tmp_path / "lyric_data.json"
    data.save(json_path)  # allow_nan=False + validate() を通過すること
    loaded = LyricData.load(json_path)
    assert len(loaded.valence_arousal) == len(mm.valence_arousal)
