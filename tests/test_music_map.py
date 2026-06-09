"""楽曲地図 (ビート/コード/声量/V-A/構造) の合成音源テスト。"""

import numpy as np
import pytest

from lyricpv import music_map


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
