"""歌詞取得 — syncedlyrics によるフォールバック付きフェッチ。

Tier 構成 (要件定義 5 章):
- T1: 逐字 LRC (enhanced)        → syncedlyrics enhanced=True
- T2: 行レベル LRC               → syncedlyrics (通常)
- T3: プレーンテキスト           → 呼び出し側がユーザー入力で供給
- T4: なし                       → phrases 空のまま (楽曲地図のみの JSON)

ボカロ曲では NetEase のカバレッジが高いため、プロバイダ順を
NetEase 優先に並べ替えるオプションを持つ。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDERS = ["Lrclib", "NetEase", "Megalobiz", "Genius"]
_VOCALOID_PROVIDERS = ["NetEase", "Lrclib", "Megalobiz", "Genius"]


def fetch_lyrics(
    title: str, artist: str, *, vocaloid: bool = False
) -> tuple[str | None, str]:
    """歌詞を検索して LRC テキストと到達 Tier を返す。

    Returns:
        (lrc_text, tier) — tier は "T1" / "T2" / "T4"。
        T3 (プレーン歌詞) は外部入力経由のためここでは返さない。
    """
    import syncedlyrics

    providers = _VOCALOID_PROVIDERS if vocaloid else _DEFAULT_PROVIDERS
    term = f"{title} {artist}".strip()

    try:
        lrc = syncedlyrics.search(term, enhanced=True, providers=providers)
        if lrc and "<" in lrc:
            return lrc, "T1"
    except Exception as e:  # ネットワーク・プロバイダ起因の失敗は次の Tier へ
        logger.warning("逐字歌詞の検索に失敗 (%s): %s", term, e)

    try:
        lrc = syncedlyrics.search(term, providers=providers)
        if lrc:
            return lrc, "T2"
    except Exception as e:
        logger.warning("同期歌詞の検索に失敗 (%s): %s", term, e)

    return None, "T4"
