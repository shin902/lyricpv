"""②' ボーカル強化 (opt-in) — 分離ボーカルからハモリ・残響を除去する。

Demucs の vocals ステムにはハモリ(コーラス)と残響(エコー)が残り、
声量ベースの歌唱区間推定を膨らませる (#3)。本モジュールは audio-separator
(UVR 系モデルのランナー) を後段に重ね、

1. カラオケ系モデル   : リードボーカルのみ抽出 (ハモリ除去)
2. DeEcho/DeReverb 系 : 残響・エコー除去

の 2 段で vocals.wav を磨く。モデルのダウンロードと推論が重いため既定 OFF
(CLI の --enhance-vocals で有効化) とし、使用モデルは meta.json に記録する。

依存は任意 extra: ``uv sync --extra enhance``
モデル一覧の確認: ``uv run audio-separator --list_models``
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 既定モデルは audio-separator のモデルレジストリ (models.json) に存在する
# ファイル名であること。日本語ポップスでの優劣は実曲で検証して差し替えてよい。
DEFAULT_KARAOKE_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
DEFAULT_DEREVERB_MODEL = "dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt"

ENHANCED_FILENAME = "vocals_enhanced.wav"

# 各段で採用するステムをファイル名 (小文字) のキーワードで選ぶ。
# モデルによってステム名が異なる (Vocals / Dry / No Reverb 等) ため複数候補を持つ。
_KARAOKE_STEM_KEYWORDS = ("vocals",)
_DEREVERB_STEM_KEYWORDS = ("dry", "noreverb", "no reverb", "noecho", "no echo")


class EnhanceError(RuntimeError):
    """ボーカル強化に失敗したときに送出される。"""


@dataclass
class EnhanceResult:
    vocals_path: Path
    models_used: list[str]


def enhance_vocals(
    vocals_path: str | Path,
    out_dir: str | Path,
    *,
    karaoke_model: str | None = DEFAULT_KARAOKE_MODEL,
    dereverb_model: str | None = DEFAULT_DEREVERB_MODEL,
) -> EnhanceResult:
    """vocals.wav にハモリ除去→残響除去を掛け ``vocals_enhanced.wav`` を作る。

    karaoke_model / dereverb_model に None を渡すとその段をスキップできる。
    中間生成物は ``<out_dir>/_enhance`` に置き、成功時に削除する。
    """
    separator_cls = _import_separator()

    vocals_path = Path(vocals_path)
    out_dir = Path(out_dir)
    work_dir = out_dir / "_enhance"
    work_dir.mkdir(parents=True, exist_ok=True)

    stages = [
        (karaoke_model, _KARAOKE_STEM_KEYWORDS),
        (dereverb_model, _DEREVERB_STEM_KEYWORDS),
    ]

    separator = separator_cls(
        output_dir=str(work_dir),
        output_format="WAV",
        log_level=logging.WARNING,
    )

    current = vocals_path
    models_used: list[str] = []
    try:
        for model, keywords in stages:
            if not model:
                continue
            separator.load_model(model_filename=model)
            outputs = separator.separate(str(current))
            current = _pick_stem(outputs, keywords, work_dir, model)
            models_used.append(model)
    except EnhanceError:
        raise
    except Exception as e:  # モデル取得失敗・推論エラー等は依存側の例外型が不定
        raise EnhanceError(f"ボーカル強化に失敗しました: {e}") from e

    if not models_used:
        raise EnhanceError("ボーカル強化のモデルが 1 つも指定されていません")

    final_path = out_dir / ENHANCED_FILENAME
    shutil.copyfile(current, final_path)
    shutil.rmtree(work_dir, ignore_errors=True)
    return EnhanceResult(vocals_path=final_path, models_used=models_used)


def _import_separator():
    try:
        from audio_separator.separator import Separator
    except ImportError as e:
        raise EnhanceError(
            "ボーカル強化には audio-separator が必要です: uv sync --extra enhance"
        ) from e
    return Separator


def _pick_stem(outputs: list[str], keywords: tuple[str, ...], work_dir: Path, model: str) -> Path:
    """分離結果のファイル群から目的のステムをファイル名キーワードで選ぶ。"""
    paths = [Path(o) if Path(o).is_absolute() else work_dir / o for o in outputs]
    if not paths:
        raise EnhanceError(f"モデル {model} が出力を生成しませんでした")
    for kw in keywords:
        for p in paths:
            if kw in p.name.lower():
                return p
    if len(paths) == 1:
        return paths[0]
    names = ", ".join(p.name for p in paths)
    raise EnhanceError(
        f"モデル {model} の出力から目的のステムを特定できませんでした: {names}"
    )
