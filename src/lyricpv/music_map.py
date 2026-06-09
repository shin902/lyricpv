"""③ 楽曲地図 — ビート / ダウンビート / 構造 / コード / 声量 / ムード(V/A)。

要件定義の既定ツール allin1 は macOS でネイティブ依存 (natten) の導入が
困難なため、MVP では librosa ベースの解析で楽曲地図を構成する。
契約A の JSON 形式さえ守れば解析器は差し替え可能 (これが契約Aの目的)。

精度の位置づけ:
- ビート: librosa の動的計画法ビートトラッカー。ポップスでは実用的。
- ダウンビート: 4 拍子を仮定し、オンセット強度が最大になる位相を選ぶ簡易推定。
- 構造: 特徴量の凝集クラスタリング + 反復・エネルギーによるサビ推定 (ヒューリスティック)。
- コード: クロマのテンプレートマッチ (maj/min 24 種)。
- V/A: 要件定義 4.2 が許容する「tempo+調性+energy の簡易プロキシ」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np

from .schema import AmplitudePoint, Beat, Chord, Segment, VAPoint

ANALYSIS_SR = 22_050
HOP = 512

# Krumhansl-Schmuckler のキープロファイル (valence 推定の長短調判定に使用)
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class MusicMap:
    beats: list[Beat] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    amplitude: list[AmplitudePoint] = field(default_factory=list)
    valence_arousal: list[VAPoint] = field(default_factory=list)
    tempo_bpm: float = 0.0


def analyze(master_path: str | Path, vocals_path: str | Path | None = None) -> MusicMap:
    """WAV マスター (+ 分離ボーカル) から楽曲地図を作る。"""
    y, sr = librosa.load(str(master_path), sr=ANALYSIS_SR, mono=True)
    duration_ms = int(len(y) / sr * 1000)

    y_harmonic = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr, hop_length=HOP)

    tempo_bpm, beat_times_ms, positions = _track_beats(y, sr)
    beats = [Beat(start_time=t, position=p) for t, p in zip(beat_times_ms, positions)]

    chords = _detect_chords(chroma, beat_times_ms, duration_ms, sr)
    segments = _detect_segments(y, chroma, sr, duration_ms)

    if vocals_path is not None and Path(vocals_path).exists():
        yv, _ = librosa.load(str(vocals_path), sr=ANALYSIS_SR, mono=True)
    else:
        yv = y  # 分離ボーカルが無い場合は全体ミックスで代用
    amplitude = _vocal_amplitude(yv, sr)

    va = _valence_arousal(y, chroma, sr, tempo_bpm)

    return MusicMap(
        beats=beats,
        chords=chords,
        segments=segments,
        amplitude=amplitude,
        valence_arousal=va,
        tempo_bpm=tempo_bpm,
    )


def _track_beats(y: np.ndarray, sr: int) -> tuple[float, list[int], list[int]]:
    """ビート時刻とダウンビート位相 (4 拍子仮定) を推定する。"""
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=HOP)
    tempo_bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)

    if len(beat_frames) == 0:
        return tempo_bpm, [], []

    # ダウンビート位相: 拍ごとのオンセット強度を 4 拍周期で平均し最大の位相を採用
    strengths = onset_env[np.clip(beat_frames, 0, len(onset_env) - 1)]
    meter = 4
    if len(strengths) >= meter:
        phase = int(np.argmax([strengths[p::meter].mean() for p in range(meter)]))
    else:
        phase = 0
    positions = [((i - phase) % meter) + 1 for i in range(len(beat_times))]
    return tempo_bpm, [int(t * 1000) for t in beat_times], positions


def _detect_chords(
    chroma: np.ndarray, beat_times_ms: list[int], duration_ms: int, sr: int
) -> list[Chord]:
    """ビート区間ごとのクロマ平均をテンプレートマッチしてコード列を得る。"""
    templates = []
    names = []
    for root in range(12):
        maj = np.zeros(12)
        maj[[root, (root + 4) % 12, (root + 7) % 12]] = 1.0
        templates.append(maj)
        names.append(_NOTE_NAMES[root])
        minor = np.zeros(12)
        minor[[root, (root + 3) % 12, (root + 7) % 12]] = 1.0
        templates.append(minor)
        names.append(_NOTE_NAMES[root] + "m")
    templates = np.array(templates)
    templates /= np.linalg.norm(templates, axis=1, keepdims=True)

    boundaries_ms = list(beat_times_ms) + [duration_ms]
    if not beat_times_ms or beat_times_ms[0] > 0:
        boundaries_ms = [0] + boundaries_ms

    raw: list[Chord] = []
    for start, end in zip(boundaries_ms[:-1], boundaries_ms[1:]):
        if end <= start:
            continue
        f0 = int(start / 1000 * sr / HOP)
        f1 = max(f0 + 1, int(end / 1000 * sr / HOP))
        seg = chroma[:, f0:f1].mean(axis=1)
        norm = np.linalg.norm(seg)
        if norm < 1e-6:
            name = "N"  # 無音・無和声区間
        else:
            name = names[int(np.argmax(templates @ (seg / norm)))]
        raw.append(Chord(start_time=start, end_time=end, name=name))

    # 同名コードの連続をマージ
    merged: list[Chord] = []
    for c in raw:
        if merged and merged[-1].name == c.name:
            merged[-1].end_time = c.end_time
        else:
            merged.append(c)
    return merged


def _detect_segments(
    y: np.ndarray, chroma: np.ndarray, sr: int, duration_ms: int
) -> list[Segment]:
    """凝集クラスタリングで構造境界を出し、反復+エネルギーでサビを推定する。"""
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)
    feats = np.vstack([librosa.util.normalize(chroma, axis=0), librosa.util.normalize(mfcc, axis=0)])

    duration_s = duration_ms / 1000
    k = int(np.clip(duration_s / 25, 4, 14))  # 1 セグメント 25 秒程度を目安
    n_frames = feats.shape[1]
    if n_frames < k * 2:
        return [Segment(start_time=0, end_time=duration_ms, label="verse")]

    bound_frames = librosa.segment.agglomerative(feats, k)
    bound_ms = [int(t * 1000) for t in librosa.frames_to_time(bound_frames, sr=sr, hop_length=HOP)]
    bound_ms = sorted(set([0] + bound_ms + [duration_ms]))

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]

    spans: list[tuple[int, int, np.ndarray, float]] = []  # (start, end, chroma平均, energy)
    for s, e in zip(bound_ms[:-1], bound_ms[1:]):
        if e - s < 2000:  # 2 秒未満の断片は隣とまとめる対象
            continue
        f0 = int(s / 1000 * sr / HOP)
        f1 = max(f0 + 1, int(e / 1000 * sr / HOP))
        spans.append((s, e, chroma[:, f0:f1].mean(axis=1), float(rms[f0:f1].mean())))
    if not spans:
        return [Segment(start_time=0, end_time=duration_ms, label="verse")]

    # 反復グループ化: クロマ平均のコサイン類似度 > 0.92 を同型とみなす
    groups: list[list[int]] = []
    for i, (_, _, ci, _) in enumerate(spans):
        placed = False
        for g in groups:
            cj = spans[g[0]][2]
            sim = float(ci @ cj / (np.linalg.norm(ci) * np.linalg.norm(cj) + 1e-9))
            if sim > 0.92:
                g.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])

    # サビ = 「複数回反復し平均エネルギーが最大」のグループ (単独最大エネルギーも候補)
    def group_score(g: list[int]) -> float:
        energy = float(np.mean([spans[i][3] for i in g]))
        return energy * (1.5 if len(g) >= 2 else 1.0)

    chorus_group = max(groups, key=group_score)
    chorus_idx = set(chorus_group)

    segments: list[Segment] = []
    for i, (s, e, _, _) in enumerate(spans):
        if i in chorus_idx:
            label = "chorus"
        elif i == 0:
            label = "intro"
        elif i == len(spans) - 1:
            label = "outro"
        else:
            label = "verse"
        segments.append(Segment(start_time=s, end_time=e, label=label))
    return segments


def _vocal_amplitude(yv: np.ndarray, sr: int, step_ms: int = 50) -> list[AmplitudePoint]:
    """分離ボーカルの RMS を 0–1 に正規化した声量エンベロープ。"""
    rms = librosa.feature.rms(y=yv, hop_length=HOP)[0]
    times_ms = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP) * 1000

    scale = np.percentile(rms, 98)
    if scale < 1e-8:
        norm = np.zeros_like(rms)
    else:
        norm = np.clip(rms / scale, 0.0, 1.0)

    points: list[AmplitudePoint] = []
    next_t = 0.0
    for t, v in zip(times_ms, norm):
        if t >= next_t:
            points.append(AmplitudePoint(time=int(t), value=round(float(v), 4)))
            next_t += step_ms
    return points


def _valence_arousal(
    y: np.ndarray, chroma: np.ndarray, sr: int, tempo_bpm: float, window_s: float = 5.0
) -> list[VAPoint]:
    """tempo+調性+energy による V/A の簡易プロキシ (要件定義 4.2 の代替案)。

    - arousal: テンポ (60–180bpm を -1〜1 に写像) と窓内 RMS の平均
    - valence: 長調/短調プロファイル相関の差 + スペクトル重心の明るさ
    """
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]

    tempo_term = float(np.clip((tempo_bpm - 120) / 60, -1.0, 1.0))
    rms_scale = np.percentile(rms, 98) + 1e-9
    centroid_scale = np.percentile(centroid, 98) + 1e-9

    frames_per_window = max(1, int(window_s * sr / HOP))
    n_frames = chroma.shape[1]

    points: list[VAPoint] = []
    for f0 in range(0, n_frames, frames_per_window):
        f1 = min(f0 + frames_per_window, n_frames)
        c = chroma[:, f0:f1].mean(axis=1)

        major_corr = max(np.corrcoef(np.roll(_MAJOR_PROFILE, k), c)[0, 1] for k in range(12))
        minor_corr = max(np.corrcoef(np.roll(_MINOR_PROFILE, k), c)[0, 1] for k in range(12))
        mode_term = float(np.clip((major_corr - minor_corr) * 3, -1.0, 1.0))
        brightness = float(np.clip(centroid[f0:f1].mean() / centroid_scale * 2 - 1, -1.0, 1.0))
        valence = float(np.clip(0.7 * mode_term + 0.3 * brightness, -1.0, 1.0))

        energy = float(np.clip(rms[f0:f1].mean() / rms_scale * 2 - 1, -1.0, 1.0))
        arousal = float(np.clip(0.5 * tempo_term + 0.5 * energy, -1.0, 1.0))

        t_ms = int(f0 * HOP / sr * 1000)
        points.append(VAPoint(time=t_ms, valence=round(valence, 3), arousal=round(arousal, 3)))
    return points
