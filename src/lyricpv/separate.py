"""② 音源分離 — Demucs (htdemucs) でボーカル / 伴奏を分離する。

Apple Silicon では MPS で実行し、未対応演算は CPU にフォールバックする
(:mod:`lyricpv.device` 参照)。分離ボーカルは後段の声量計測と
歌詞アライメント補正の入力になる。

モデルは既定で htdemucs。要件定義は htdemucs_ft (4 モデルのバギングで
約 4 倍遅い) を推奨しているため、品質優先時は model_name で切り替える。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
import torch  # noqa: E402

# torch のロード前に PYTORCH_ENABLE_MPS_FALLBACK を設定する必要があるため、
# device モジュールを必ず先に import する (device.py の docstring 参照)
from .device import pick_device

DEFAULT_MODEL = "htdemucs"

VOCALS_FILENAME = "vocals.wav"
ACCOMPANIMENT_FILENAME = "accompaniment.wav"


class SeparationError(RuntimeError):
    """音源分離に失敗したときに送出される。"""


@dataclass
class SeparationResult:
    vocals_path: Path
    accompaniment_path: Path
    sample_rate: int
    device_used: str


def _load_wav(path: Path) -> tuple[torch.Tensor, int]:
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return torch.from_numpy(data.T.copy()), sr  # (ch, time)


def separate(
    wav_path: str | Path,
    out_dir: str | Path,
    *,
    model_name: str = DEFAULT_MODEL,
    device: str | None = None,
) -> SeparationResult:
    """WAV マスターをボーカルと伴奏に分離して保存する。

    MPS で失敗した場合は CPU で 1 回だけリトライする
    (MPS の対応状況はモデル・torch のバージョン依存のため)。
    """
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    wav_path = Path(wav_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model = get_model(model_name)
    model.eval()

    wav, sr = _load_wav(wav_path)
    if sr != model.samplerate:
        import torchaudio

        wav = torchaudio.functional.resample(wav, sr, model.samplerate)
        sr = model.samplerate
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)

    # demucs は学習時の統計に合わせた正規化を期待する
    ref = wav.mean(0)
    normalized = (wav - ref.mean()) / (ref.std() + 1e-8)

    dev = pick_device(device)
    try:
        sources = _apply(apply_model, model, normalized, dev)
        device_used = dev.type
    except (RuntimeError, NotImplementedError) as e:
        if dev.type == "cpu":
            raise SeparationError(f"音源分離に失敗しました: {e}") from e
        try:
            sources = _apply(apply_model, model, normalized, torch.device("cpu"))
        except (RuntimeError, NotImplementedError) as e2:
            raise SeparationError(f"音源分離に失敗しました (CPU リトライも失敗): {e2}") from e2
        device_used = "cpu"

    sources = sources * (ref.std() + 1e-8) + ref.mean()

    names = list(model.sources)  # 例: ['drums', 'bass', 'other', 'vocals']
    if "vocals" not in names:
        raise SeparationError(f"モデル {model_name} に vocals ステムがありません: {names}")
    vocal_idx = names.index("vocals")
    vocals = sources[vocal_idx]
    accompaniment = sources.sum(dim=0) - vocals

    vocals_path = out_dir / VOCALS_FILENAME
    acc_path = out_dir / ACCOMPANIMENT_FILENAME
    sf.write(vocals_path, vocals.numpy().T, sr)
    sf.write(acc_path, accompaniment.numpy().T, sr)

    return SeparationResult(
        vocals_path=vocals_path,
        accompaniment_path=acc_path,
        sample_rate=sr,
        device_used=device_used,
    )


def _apply(apply_model, model, wav: torch.Tensor, device: torch.device) -> torch.Tensor:
    with torch.no_grad():
        out = apply_model(model, wav[None], device=device, split=True, overlap=0.25, progress=False)
    return out[0].cpu()  # (sources, ch, time)
