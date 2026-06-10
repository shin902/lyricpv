"""解析に使うデバイス (MPS / CUDA / CPU) の自動選択。

macOS では Apple Silicon の MPS を優先する。一部の演算が MPS 未対応でも
処理が止まらないよう、torch の読み込み前に CPU フォールバックを有効化する
必要がある — そのためこのモジュールは torch を import する前に
環境変数を設定する (lyricpv 内では必ず本モジュール経由で torch に触れること)。
"""

from __future__ import annotations

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402


def pick_device(preferred: str | None = None) -> torch.device:
    """利用可能な最良のデバイスを返す。

    Args:
        preferred: "mps" / "cuda" / "cpu" を明示指定する場合。
            指定デバイスが利用不可ならフォールバックする。
    """
    if preferred == "cpu":
        return torch.device("cpu")
    if preferred in (None, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    if preferred in (None, "cuda") and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")
