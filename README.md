# lyricpv — 文字PV生成 SDK

任意の楽曲(YouTube / ローカル音源)から **TextAlive App API 互換の解析データ**を生成し、プログラムから文字PV(キネティックタイポグラフィMV)を作れるようにする SDK です。

設計思想は TextAlive と同じく **「SDK はデータとライフサイクルの供給に徹し、描画は行わない」**。アニメーションは利用者が p5.js / three.js / canvas 等で自由に実装します。

要件定義: [docs/lyric-pv-sdk-requirements.md](docs/lyric-pv-sdk-requirements.md)

## 構成

```
[オフライン / Python / 1曲につき1回]
  取得(yt-dlp) → 分離(Demucs/MPS) → 楽曲地図(librosa) → 歌詞整合(モーラ按分)
      ↓
  契約A: TextAlive 互換 JSON (lyric_data.json)
      ↓
[オンライン / JS / リアルタイム]
  契約B: ランタイム SDK (sdk/lyric-player.mjs) → 利用者のレンダラ
```

| 層 | 場所 | 役割 |
|---|---|---|
| オフライン解析 | `src/lyricpv/` (Python) | 音源取得・分離・楽曲地図・歌詞タイミング付与 → JSON |
| ランタイム SDK | `sdk/lyric-player.mjs` (JS) | JSON 読込・音源同期・データ参照 API |

## セットアップ

必要なもの: macOS (Apple Silicon 推奨・MPS 使用) / [uv](https://docs.astral.sh/uv/) / ffmpeg / Node.js (SDK テストのみ)

```bash
brew install ffmpeg        # 未導入なら
uv sync                    # Python 3.12 venv + 依存を構築
```

## 使い方

### 1. 楽曲を解析する(オフライン)

```bash
# YouTube から(歌詞は LRCLIB 等から自動検索)
uv run lyricpv analyze "https://www.youtube.com/watch?v=XXXX" -o data/songs/mysong

# ローカル音源 + 手持ちの歌詞ファイル(LRC または プレーンテキスト)
uv run lyricpv analyze song.wav --lyrics song.lrc --title "曲名" --artist "アーティスト"

# ボカロ曲は NetEase 優先で歌詞検索
uv run lyricpv analyze "https://..." --vocaloid

# 高品質分離(約4倍遅い) / デバイス強制
uv run lyricpv analyze song.wav --model htdemucs_ft --device cpu

# ボーカル強化: 分離ボーカルからハモリ・残響を除去して歌唱区間推定を安定させる
# (重い処理のため既定 OFF。要: uv sync --extra enhance)
uv run lyricpv analyze song.wav --enhance-vocals

# 強制アラインメント: 行内の文字タイミングを音声から実測して補正(サビずれ対策の本命)
# (重い処理のため既定 OFF。要: uv sync --extra refine。--enhance-vocals と併用可)
uv run lyricpv analyze song.wav --refine-align

# 補正のチューニング(既定値は実曲の実測分布から決定。詳細: --help)
#   --refine-pad          行窓の探索パディング ms(LRC が全体的にずれている曲は広げる)
#   --refine-min-score    潰れ実測を捨てる文字スコア閾値 / --refine-min-match 行の最低マッチ率
#   --refine-max-squashed 崩壊判定(超えた行は按分のまま)
uv run lyricpv analyze song.wav --refine-align --refine-pad 600 --refine-max-squashed 2

# enhance のモデル差し替え('none' で段をスキップ。一覧: uv run audio-separator --list_models)
uv run lyricpv analyze song.wav --enhance-vocals --dereverb-model none

# 対話モード(YouTube の装飾タイトル/チャンネル名で歌詞検索が外れるとき)
#   ・取得後に title/artist を確認・修正してから検索
#   ・ヒットした歌詞の冒頭を表示し、違えば打ち直して再検索 (Y/n/r)
uv run lyricpv analyze -i "https://www.youtube.com/watch?v=XXXX"
uv run lyricpv analyze            # source も省略すると URL から対話入力
```

出力(`data/songs/<名前>/`):

- `lyric_data.json` — **契約A**: TextAlive 互換 JSON(これだけが配布対象)
- `master.wav` / `vocals.wav` / `accompaniment.wav` — 解析用中間ファイル(**再配布しない**)
- `meta.json` — 歌詞 Tier・使用デバイス・テンポ等の解析メタ

### 2. WebUI から解析する(任意)

ブラウザから曲の投入・進捗確認・JSON ダウンロードができる解析フロントエンドも同梱しています(描画・アニメーション機能はありません)。

```bash
uv sync --extra webui
uv run lyricpv serve --port 8000   # → http://127.0.0.1:8000
```

YouTube URL またはサーバー上のファイルパスを入力し、歌詞(LRC/プレーン)を任意で添えて「解析を開始」。完了すると一覧から `lyric_data.json` を取得できます。

> ⚠️ WebUI はローカルツール想定です(既定で 127.0.0.1 バインド)。`source` にサーバー上の任意ファイルパスや任意 URL を受け付けるため、`--host 0.0.0.0` 等で外部公開すると第三者がサーバー上のファイルを解析対象にしたり、サーバーから任意 URL へアクセスさせたり(SSRF)できてしまいます。公開しないでください。

### 3. ランタイム SDK で文字PVを作る(オンライン)

```html
<audio id="audio" src="master.wav"></audio>
<script type="module">
  import { Player, htmlAudioAdapter } from "./sdk/lyric-player.mjs";

  const player = new Player();
  const json = await (await fetch("lyric_data.json")).json();
  await player.load(json, htmlAudioAdapter(document.getElementById("audio")));

  player.on("timeupdate", (ms) => {
    const char  = player.currentChar(ms);        // 今発声中の文字
    const beat  = player.findBeat(ms);           // 直近の拍 (position: 1〜4)
    const onSabi = player.findChorus(ms) !== null; // サビ中か
    const amp   = player.getVocalAmplitude(ms);  // 声量 0–1
    const mood  = player.getValenceArousal(ms);  // ムード -1〜1
    // → ここから先の「絵」はあなたのレンダラの仕事
  });

  player.play();
</script>
```

API 詳細は [docs/sdk-reference.md](docs/sdk-reference.md) を参照。動作するサンプル一式は [examples/simple-player](examples/simple-player) にあります。

## テスト

```bash
uv run pytest                          # Python 側 (ネットワーク・GPU 不使用)
node --test sdk/lyric-player.test.mjs  # ランタイム SDK 側

# 音源分離の実機テスト(モデルDL数百MB + MPS 実計算を伴う)
LYRICPV_RUN_SEPARATION_TESTS=1 uv run pytest tests/test_separate.py
```

## 歌詞取得のフォールバック

| Tier | ソース | 精度 |
|---|---|---|
| T1 | 逐字 LRC (enhanced) | word 時刻は実測・char のみ按分 |
| T2 | 行 LRC | 行窓内をモーラ数比で按分 |
| T3 | プレーン歌詞 (`--lyrics`) | 声量から歌唱区間を推定して按分(粗い叩き台) |
| T4 | なし | 楽曲地図のみの JSON |

按分誤差(メリスマ/ロングトーン等)は SDK の `applyOverrides()` で手動補正できます。

## 著作権・利用上の注意

lyricpv は汎用の楽曲解析ツールです。**何を解析し、成果物をどう使うかの権利確認は利用者の責任**です。以下は法的助言ではなく、設計上の想定を示すものです。

### 入力(取得)について

- **YouTube からの取得** — yt-dlp によるダウンロードは YouTube 利用規約が禁止する行為です(著作権侵害とは別軸の規約違反)。日本法では私的複製の範囲に収まりうるものの、完全にクリーンな行為ではないことを理解した上で、**個人のローカルでの解析・鑑賞に限定**してください
- **歌詞の自動取得**(LRCLIB / NetEase) — 歌詞も著作物です。取得した歌詞データは同様に個人利用の範囲に留めてください
- 推奨は権利的にクリーンな入力です: **自作曲 / piapro 等で二次利用許諾された楽曲 / CC・フリー音源 / 正規購入したローカル音源**

### 出力(配布・公開)について

- ダウンロードした音源・分離ステム(`master.wav` / `vocals.wav` / `accompaniment.wav`)は**解析処理内に留め、配布物・公開リポジトリに含めない**こと。これは原盤の再配布にあたります
- `lyric_data.json` も**歌詞テキストを含む**ため、無条件に配布可能ではありません。配布・公開してよいのは二次利用可ライセンスの楽曲(piapro 等)か自作曲のもののみ
- 作った文字PVを**公の場で上映・配信する場合は私的利用の範囲を超えます**。楽曲の利用許諾(作者の利用規約、プラットフォームの包括契約の範囲等)を確認してください。ライセンス処理済みの楽曲で動く公式の仕組みとして [TextAlive App API](https://developer.textalive.jp/) があります

### ツールとしての位置づけ

yt-dlp や歌詞取得機能をリポジトリに含むこと自体は、合法用途を持つ汎用ツールの範囲です(yt-dlp 本体と同じ整理)。問題になるのは常に「何に使ったか」「何を配ったか」であり、本 README の注意はそのための線引きです。

## ドキュメント

- [docs/architecture.md](docs/architecture.md) — 設計判断と要件定義からの調整点
- [docs/sdk-reference.md](docs/sdk-reference.md) — ランタイム SDK API リファレンス
- [docs/lyric-pv-sdk-requirements.md](docs/lyric-pv-sdk-requirements.md) — 要件定義

## クレジット

本プロジェクトは [TextAlive App API](https://developer.textalive.jp/) にインスパイアされ、API の形も意図的に似せています。

- 「SDK はデータとライフサイクルの供給に徹し、描画は行わない」という設計思想そのもの
- `findBeat(time)` / `findChord(time)` — TextAlive の `IPlayer.findBeat` / `findChord` と同名・同概念
- `currentChar(ms)` / `currentWord(ms)` / `currentPhrase(ms)` — TextAlive の `IVideo.findChar` / `findWord` / `findPhrase`(指定時刻に発声中の文字・単語・フレーズを返す)を、lyricpv では `current*` という命名に変えて採用
- `player.on(event, cb)` による購読形式 — TextAlive の `Player` のイベントリスナ(`onPlay` / `onPause` / `onTimeUpdate` 等)のスタイルを踏襲
