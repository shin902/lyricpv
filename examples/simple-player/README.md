# simple-player — 最小構成のプレイヤーサンプル

`sdk/lyric-player.mjs`(契約B ランタイムSDK)を使った、再生・一時停止・停止・シークと、現在発声中の文字のハイライト表示ができる最小構成のサンプルです。

## 使い方

1. 曲を解析して契約A JSON と音源を生成する(未実施なら)

   ```bash
   uv run lyricpv analyze "https://www.youtube.com/watch?v=XXXX" -o data/songs/<name>
   ```

2. 生成された `lyric_data.json` と `master.wav` をこのディレクトリにコピーする

   ```bash
   cp data/songs/<name>/lyric_data.json data/songs/<name>/master.wav examples/simple-player/
   ```

3. 同梱の `serve.py`(標準ライブラリのみ、Range リクエスト対応)でリポジトリルートを配信する(`fetch()` でJSONを読み込むため `file://` 直開きは不可)

   ```bash
   python3 examples/simple-player/serve.py        # ポート省略時は 8000
   # → http://localhost:8000/examples/simple-player/
   ```

   > **注意**: `python3 -m http.server` は使わないこと。Range リクエストに
   > 非対応のため、ブラウザが WAV をシーク不能と判定し、シークバーが効かなくなる
   > (`audio.currentTime` の設定が常に 0 へ戻る)。

4. ブラウザで `http://localhost:8000/examples/simple-player/` を開く

## カスタマイズ

このディレクトリをコピーして自分のレンダラに書き換えてください。`player.on('timeupdate', ...)` 内で `currentChar` / `findBeat` / `getVocalAmplitude` などのAPIから絵を組み立てます。詳細は [docs/sdk-reference.md](../../docs/sdk-reference.md) を参照。

`sdk` はリポジトリ直下の `sdk/` へのシンボリックリンクです。SDK側に修正が入った場合もコピー不要でそのまま反映されます。
