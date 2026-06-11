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

API 詳細は [docs/sdk-reference.md](docs/sdk-reference.md) を参照。

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

## 著作権上の注意

- ダウンロードした音源・分離ステムは**解析処理内に留め、配布物に含めない**こと
- 配布してよいのは `lyric_data.json`(解析データ)のみ。歌詞テキストを含むため、二次利用可ライセンスの楽曲(piapro 等)を選ぶこと

## ドキュメント

- [docs/architecture.md](docs/architecture.md) — 設計判断と要件定義からの調整点
- [docs/sdk-reference.md](docs/sdk-reference.md) — ランタイム SDK API リファレンス
- [docs/lyric-pv-sdk-requirements.md](docs/lyric-pv-sdk-requirements.md) — 要件定義
