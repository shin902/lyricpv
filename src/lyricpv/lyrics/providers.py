"""歌詞取得の Strategy パターン実装。

各 ``LyricsProvider`` は「あるソースから歌詞を取得する」責務のみを持ち、
呼び出し側は ``build_provider_chain()`` でチェーンを組み、
``resolve_lyrics()`` で順に試して最初に見つかった結果を採用する。

Tier 構成 (要件定義 5 章):
- T1: 逐字 LRC (enhanced)        → syncedlyrics enhanced=True / ユーザー供給の逐字 LRC
- T2: 行レベル LRC               → syncedlyrics (通常) / ユーザー供給の行 LRC
- T3: プレーンテキスト           → ユーザー供給のプレーン歌詞
- T4: なし                       → phrases 空のまま (楽曲地図のみの JSON)
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Protocol

from .lrc import LyricLine, is_synced, is_word_synced, parse_lrc

logger = logging.getLogger(__name__)

# ボカロ曲では NetEase のカバレッジが高いため、プロバイダ順を
# NetEase 優先に並べ替える。
_DEFAULT_PROVIDER_NAMES = ["Lrclib", "NetEase", "Megalobiz", "Genius"]
_VOCALOID_PROVIDER_NAMES = ["NetEase", "Lrclib", "Megalobiz", "Genius"]


class LyricsResult(NamedTuple):
    """歌詞取得結果。

    ``lrc_text`` が ``None`` の場合は「未検出 (T4)」を表す。
    """

    lrc_text: str | None
    tier: str  # "T1" / "T2" / "T3" / "T4"


class LyricsProvider(Protocol):
    """歌詞取得ソースの共通インタフェース。"""

    name: str

    def fetch(self, title: str, artist: str) -> LyricsResult | None:
        """歌詞を検索する。この曲が見つからなければ ``None`` (次のプロバイダへ)。"""
        ...


class SyncedLyricsProvider:
    """syncedlyrics ライブラリ経由で外部プロバイダ群を検索する。

    現行実装は「全プロバイダで enhanced 検索 → 見つからなければ全プロバイダで
    通常検索」という 2 パス構成になっている。プロバイダ 1 件ごとに
    enhanced→通常を試す構成にすると探索順 (ひいては結果) が変わってしまうため、
    ここでは 1 つの Provider が両パスをまとめて担う形にして現行挙動を維持する。
    """

    def __init__(self, provider_names: list[str], *, name: str = "syncedlyrics") -> None:
        self.name = name
        self._provider_names = provider_names

    def fetch(self, title: str, artist: str) -> LyricsResult | None:
        import syncedlyrics

        term = f"{title} {artist}".strip()

        try:
            lrc = syncedlyrics.search(term, enhanced=True, providers=self._provider_names)
            if lrc and "<" in lrc:
                return LyricsResult(lrc, "T1")
        except Exception as e:  # ネットワーク・プロバイダ起因の失敗は次の Tier へ
            logger.warning("逐字歌詞の検索に失敗 (%s): %s", term, e)

        try:
            lrc = syncedlyrics.search(term, providers=self._provider_names)
            if lrc:
                return LyricsResult(lrc, "T2")
        except Exception as e:
            logger.warning("同期歌詞の検索に失敗 (%s): %s", term, e)

        return None


def parse_user_text(lyrics_text: str) -> tuple[list[LyricLine], str]:
    """ユーザー供給テキストを1回のパースで (LyricLine リスト, tier) に変換する。

    align() の経路選択 (is_word_synced) と同じ基準で Tier を判定する。
    一部の行のみ逐字タグを持つ LRC は align() 側で T2 経路になるため、
    T1 と報告すると meta.json と実際の整合が食い違う。
    """
    lines = parse_lrc(lyrics_text)
    if is_synced(lines):
        tier = "T1" if is_word_synced(lines) else "T2"
    else:
        tier = "T3"
    return lines, tier


def build_provider_chain(vocaloid: bool) -> list[LyricsProvider]:
    """歌詞取得に使うプロバイダのチェーンを組み立てる。

    syncedlyrics 経由のチェーンを使い、``vocaloid=True`` なら
    NetEase を優先する順序に並べ替える。
    """
    provider_names = _VOCALOID_PROVIDER_NAMES if vocaloid else _DEFAULT_PROVIDER_NAMES
    return [SyncedLyricsProvider(provider_names)]


def resolve_lyrics(chain: list[LyricsProvider], title: str, artist: str) -> LyricsResult:
    """チェーンを先頭から順に試し、最初に見つかった結果を返す。全滅なら T4。"""
    for provider in chain:
        result = provider.fetch(title, artist)
        if result is not None:
            return result
    return LyricsResult(None, "T4")
