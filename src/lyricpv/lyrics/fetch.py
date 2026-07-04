"""歌詞取得 — syncedlyrics によるフォールバック付きフェッチ。

Tier 構成 (要件定義 5 章):
- T1: 逐字 LRC (enhanced)        → syncedlyrics enhanced=True
- T2: 行レベル LRC               → syncedlyrics (通常)
- T3: プレーンテキスト           → 呼び出し側がユーザー入力で供給
- T4: なし                       → phrases 空のまま (楽曲地図のみの JSON)

実体は providers.py の Strategy チェーンに委譲する薄いラッパー。
公開 API (関数シグネチャ・戻り値) は互換性のため維持する。
"""

from __future__ import annotations

from .providers import build_provider_chain, resolve_lyrics


def fetch_lyrics(title: str, artist: str, *, vocaloid: bool = False) -> tuple[str | None, str]:
    """歌詞を検索して LRC テキストと到達 Tier を返す。

    Returns:
        (lrc_text, tier) — tier は "T1" / "T2" / "T4"。
        T3 (プレーン歌詞) は外部入力経由のためここでは返さない。
    """
    chain = build_provider_chain(None, vocaloid)
    result = resolve_lyrics(chain, title, artist)
    return result.lrc_text, result.tier
