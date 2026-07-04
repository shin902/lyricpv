# ランタイム SDK リファレンス(契約B)

`sdk/lyric-player.mjs` — 依存ゼロの ES module。ブラウザ / Node の両方で動作する。

TextAlive App API のインターフェースを踏襲しており、**描画は一切行わない**。`timeupdate` と各参照 API を購読し、「いつ・何が・どのくらいの強さで」を自前のレンダラで絵にする。

## Player

```js
import { Player, htmlAudioAdapter, manualClockAdapter } from "./lyric-player.mjs";
const player = new Player({ tickIntervalMs: 16 }); // 省略可
```

### ライフサイクル

| メソッド | 説明 |
|---|---|
| `await load(json, audio)` | 契約A JSON と音源アダプタを読み込む。完了で `ready` 発火。渡した `json` は変更されない (Player が専有コピーを保持する)。再生中に呼ぶと旧音源を停止し、tick も引き継がれない |
| `on(event, cb)` / `off(event, cb)` | イベント購読。`"ready" \| "timeupdate" \| "play" \| "pause" \| "end"` |
| `play()` / `pause()` | 再生制御。再生中は `tickIntervalMs` ごとに `timeupdate(positionMs)` が発火 |
| `seek(ms)` | 解析データ時間軸での移動(offsetMs 補正は内部で処理) |
| `tick()` | `timeupdate` を 1 回手動発火。requestAnimationFrame 駆動や MP4 書き出しで使う |
| `position` | 現在の再生位置 [ms](offsetMs 補正済み・解析データと同一時間軸) |

### 現在時刻のデータ参照

すべて引数は ms、該当なしは `null` を返す。内部は二分探索で O(log n)。

| メソッド | 返り値 |
|---|---|
| `findBeat(ms)` | 直近の拍 `{startTime, position}`(position は小節内 1〜4) |
| `findChord(ms)` | `{startTime, endTime, name}`(例 `"C"`, `"Am"`, 無和声は `"N"`) |
| `findSegment(ms)` | `{startTime, endTime, label}` |
| `findChorus(ms)` | ms がサビ区間ならその Segment、それ以外は `null` |
| `currentPhrase(ms)` | 発声中のフレーズ(行) |
| `currentWord(ms)` | 発声中の単語(`pos` に品詞: 名詞/動詞 等) |
| `currentChar(ms)` | 発声中の文字 |

### 表現用パラメータ

| メソッド | 返り値 |
|---|---|
| `getVocalAmplitude(ms)` | 声量 0–1(データ点間は線形補間) |
| `getValenceArousal(ms)` | `{valence, arousal}` 各 -1〜1(直近値ホールド) |

### 手動上書きフック

自動生成タイミングは「叩き台」。メリスマ等で按分がずれた箇所を nudge する。

```js
player.applyOverrides([
  { path: [0, 2],    endTime: 24800 },          // phrases[0].words[2]
  { path: [0, 2, 1], endTime: 24800 },          // …….chars[1]
  { path: [0, 3],    startTime: 24800 },        // 隣接 word の境界も合わせて動かす
  { segment: 4,      startTime: 61000 },        // segments[4] の境界補正
]);
player.setOffset(120);   // 音源と解析データの頭ズレ校正 [ms]
```

区間を重ねたまま放置すると参照系は「より遅く始まった方」を返すため、境界は両側セットで動かすこと。

### その他

| プロパティ | 説明 |
|---|---|
| `data` | 読み込み済みの契約A JSON(読み取り用) |
| `songDurationMs` | 曲の長さ [ms] |
| `offsetMs` | 現在のオフセット補正値 |

## 音源アダプタ

`load()` の第2引数。次の形を満たせば何でもよい(YouTube IFrame Player も同形に包めば使える):

```ts
interface AudioAdapter {
  play(): void;
  pause(): void;
  seekTo(ms: number): void;
  getPositionMs(): number;
  onEnded?(cb: () => void): void;
}
```

### 同梱アダプタ

| 関数 | 用途 |
|---|---|
| `htmlAudioAdapter(audioEl)` | `<audio>` 要素を包む(インタラクティブ Web アプリ用) |
| `manualClockAdapter(durationMs)` | 手動クロック。`advance(ms)` で時間を進める。テスト・フレーム逐次レンダリング(MP4 書き出し)用 |

## MP4 書き出し

`tick()` を実時間と無関係に駆動して、フレーム単位で確定的に書き出す。SDK は描画を行わないため、Canvas へのレンダリングとピクセル保存は呼び出し側の責務。

### `sdk/frame-driver.mjs`(環境非依存)

| 関数 | 説明 |
|---|---|
| `frameTimestamps(durationMs, fps)` | 0〜durationMs を fps 刻みで分割したタイムスタンプ列(末尾フレーム含む) |
| `renderFrames(player, clock, {fps, onFrame})` | `player.seek(ms)` → `await onFrame({index, ms, frameCount})` をフレーム数分繰り返す。`seek()` が `offsetMs` を補正し `timeupdate` を発火する。`clock` は `manualClockAdapter()` の戻り値 |

### `sdk/ffmpeg-export.mjs`(Node 専用、ffmpeg パイプライン経路。フレーム精度優先)

| 関数 | 説明 |
|---|---|
| `exportFramesToMp4(player, clock, {fps, framePattern, writeFrame, audioPath, outPath})` | `renderFrames()` で駆動しつつ `writeFrame()` で各フレームを連番画像として保存、完了後 ffmpeg で音源と合成して MP4 化 |
| `muxFramesToMp4({framePattern, fps, audioPath, outPath})` | 既に書き出し済みの連番画像 + 音源を ffmpeg で MP4 化(低レベル API) |
| `muxVideoAudio({videoPath, audioPath, outPath})` | 既存動画(MediaRecorder 経路の WebM 等)に音源を後付けミキシング |

ffmpeg / ffprobe が PATH にあることを前提とする。

```js
import { readFileSync, writeFileSync } from "node:fs";
import { createCanvas } from "canvas"; // npm: node-canvas (このSDKの依存ではなく利用者側で追加する)
import { Player, manualClockAdapter } from "./lyric-player.mjs";
import { exportFramesToMp4 } from "./ffmpeg-export.mjs";

const json = JSON.parse(readFileSync("lyric_data.json", "utf8")); // 契約A (lyricpv analyze の出力)
const player = new Player();
const clock = manualClockAdapter(json.song.durationMs);
await player.load(json, clock);

const canvas = createCanvas(1920, 1080);
const ctx = canvas.getContext("2d");

await exportFramesToMp4(player, clock, {
  fps: 30,
  framePattern: "out/frame-%05d.png",
  audioPath: "master.wav",
  outPath: "out.mp4",
  writeFrame: ({ index, ms }) => {
    const char = player.currentChar(ms);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (char) ctx.fillText(char.char, canvas.width / 2, canvas.height / 2); // 描画はあなたの実装
    const path = `out/frame-${String(index).padStart(5, "0")}.png`;
    writeFileSync(path, canvas.toBuffer("image/png")); // ピクセル保存もあなたの実装(ここでは node-canvas の例)
  },
});
```

### `sdk/stream-export.mjs`(ブラウザ専用、MediaRecorder 経路。簡便だが実時間駆動でジッタの余地あり)

| 関数 | 説明 |
|---|---|
| `recordCanvasStream(player, clock, {canvas, fps, mimeType, onFrame})` | `HTMLCanvasElement.captureStream()` + `MediaRecorder` で WebM の `Blob` を返す(`OffscreenCanvas` は非対応)。映像のみのため音源は `muxVideoAudio()` で後付け |

## 最小利用例

```js
import { Player, htmlAudioAdapter } from "./lyric-player.mjs";

const player = new Player();
const json = await (await fetch("lyric_data.json")).json();
await player.load(json, htmlAudioAdapter(document.querySelector("audio")));

player.on("timeupdate", (ms) => {
  const char = player.currentChar(ms);
  if (char) {
    const scale = 1 + player.getVocalAmplitude(ms);      // 声量で文字を脈動
    const hot   = player.findChorus(ms) ? "#f33" : "#fff"; // サビで色変え
    render(char.char, scale, hot);                        // ← 描画はあなたの実装
  }
});
player.play();
```
