/**
 * 契約B ランタイム SDK のテスト (node --test sdk/ で実行)。
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { Player, manualClockAdapter } from "./lyric-player.mjs";

/** テスト用の契約A フィクスチャ。 */
function fixture() {
  return {
    song: {
      title: "テスト曲",
      artist: "テスト歌手",
      durationMs: 60_000,
      source: { type: "file", id: "test.wav", offsetMs: 0 },
    },
    phrases: [
      {
        startTime: 1000,
        endTime: 3000,
        text: "夜に",
        words: [
          {
            startTime: 1000,
            endTime: 2000,
            text: "夜",
            pos: "名詞",
            chars: [{ startTime: 1000, endTime: 2000, char: "夜" }],
          },
          {
            startTime: 2000,
            endTime: 3000,
            text: "に",
            pos: "助詞",
            chars: [{ startTime: 2000, endTime: 3000, char: "に" }],
          },
        ],
      },
      {
        startTime: 5000,
        endTime: 7000,
        text: "声",
        words: [
          {
            startTime: 5000,
            endTime: 7000,
            text: "声",
            pos: "名詞",
            chars: [{ startTime: 5000, endTime: 7000, char: "声" }],
          },
        ],
      },
    ],
    beats: [
      { startTime: 0, position: 1 },
      { startTime: 500, position: 2 },
      { startTime: 1000, position: 3 },
      { startTime: 1500, position: 4 },
      { startTime: 2000, position: 1 },
    ],
    chords: [
      { startTime: 0, endTime: 2000, name: "C" },
      { startTime: 2000, endTime: 4000, name: "Am" },
    ],
    segments: [
      { startTime: 0, endTime: 4000, label: "verse" },
      { startTime: 4000, endTime: 8000, label: "chorus" },
    ],
    amplitude: [
      { time: 0, value: 0.0 },
      { time: 1000, value: 1.0 },
      { time: 2000, value: 0.5 },
    ],
    valenceArousal: [
      { time: 0, valence: -0.5, arousal: 0.2 },
      { time: 5000, valence: 0.8, arousal: 0.9 },
    ],
  };
}

async function loadedPlayer() {
  const player = new Player();
  const clock = manualClockAdapter(60_000);
  await player.load(fixture(), clock);
  return { player, clock };
}

test("load は ready イベントを発火する", async () => {
  const player = new Player();
  let ready = false;
  player.on("ready", () => {
    ready = true;
  });
  await player.load(fixture(), manualClockAdapter(60_000));
  assert.equal(ready, true);
});

test("load は不正な JSON を拒否する", async () => {
  const player = new Player();
  await assert.rejects(() => player.load({}, manualClockAdapter(1000)));
});

test("load は渡された JSON を破壊的に変更しない", async () => {
  const player = new Player();
  const json = fixture();
  const before = JSON.parse(JSON.stringify(json));

  await player.load(json, manualClockAdapter(60_000));
  player.applyOverrides([{ path: [0, 0], endTime: 2400 }]);

  assert.deepEqual(json, before); // 呼び出し側の JSON は無変更
  assert.notEqual(player.data.phrases[0].words[0].endTime, before.phrases[0].words[0].endTime);
});

test("load は schemaVersion 欠落を無警告で許容する", async () => {
  const player = new Player();
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (msg) => warnings.push(msg);
  try {
    await player.load(fixture(), manualClockAdapter(60_000));
  } finally {
    console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
});

test("load は schemaVersion のメジャーバージョン一致時に無警告", async () => {
  const player = new Player();
  const json = { ...fixture(), schemaVersion: "1.0" };
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (msg) => warnings.push(msg);
  try {
    await player.load(json, manualClockAdapter(60_000));
  } finally {
    console.warn = originalWarn;
  }
  assert.deepEqual(warnings, []);
});

test("load は schemaVersion のメジャーバージョン不一致で警告する", async () => {
  const player = new Player();
  const json = { ...fixture(), schemaVersion: "2.0" };
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (msg) => warnings.push(msg);
  try {
    await player.load(json, manualClockAdapter(60_000));
  } finally {
    console.warn = originalWarn;
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /schemaVersion/);
});

test("findBeat は直近の拍を返す", async () => {
  const { player } = await loadedPlayer();
  assert.equal(player.findBeat(0).position, 1);
  assert.equal(player.findBeat(750).startTime, 500);
  assert.equal(player.findBeat(-1), null);
});

test("findChord / findSegment は区間を判定する", async () => {
  const { player } = await loadedPlayer();
  assert.equal(player.findChord(1999).name, "C");
  assert.equal(player.findChord(2000).name, "Am");
  assert.equal(player.findChord(4000), null);
  assert.equal(player.findSegment(100).label, "verse");
});

test("findChorus はサビ区間のみ返す", async () => {
  const { player } = await loadedPlayer();
  assert.equal(player.findChorus(1000), null);
  assert.equal(player.findChorus(5000).label, "chorus");
});

test("currentPhrase / currentWord / currentChar の境界判定", async () => {
  const { player } = await loadedPlayer();
  assert.equal(player.currentPhrase(1500).text, "夜に");
  assert.equal(player.currentPhrase(4000), null); // フレーズ間
  assert.equal(player.currentWord(1999).text, "夜");
  assert.equal(player.currentWord(2000).text, "に");
  assert.equal(player.currentChar(2500).char, "に");
  assert.equal(player.currentChar(0), null);
});

test("getVocalAmplitude は線形補間する", async () => {
  const { player } = await loadedPlayer();
  assert.equal(player.getVocalAmplitude(500), 0.5);
  assert.equal(player.getVocalAmplitude(1500), 0.75);
  assert.equal(player.getVocalAmplitude(99_999), 0.5); // 最終点以降は最終値
  assert.equal(player.getVocalAmplitude(-10), 0);
});

test("getValenceArousal は直近の値を返す", async () => {
  const { player } = await loadedPlayer();
  assert.deepEqual(player.getValenceArousal(100), { valence: -0.5, arousal: 0.2 });
  assert.deepEqual(player.getValenceArousal(6000), { valence: 0.8, arousal: 0.9 });
  assert.deepEqual(player.getValenceArousal(-1), { valence: 0, arousal: 0 });
});

test("play / pause / seek / position と手動クロック", async () => {
  const { player, clock } = await loadedPlayer();
  const events = [];
  player.on("play", () => events.push("play"));
  player.on("pause", () => events.push("pause"));

  player.play();
  clock.advance(1000);
  assert.equal(player.position, 1000);
  player.pause();
  player.seek(5000);
  assert.equal(player.position, 5000);
  assert.deepEqual(events, ["play", "pause"]);
});

test("end イベントは曲末で発火する", async () => {
  const { player, clock } = await loadedPlayer();
  let ended = false;
  player.on("end", () => {
    ended = true;
  });
  player.play();
  clock.advance(60_000);
  assert.equal(ended, true);
});

test("offsetMs は position と seek に反映される", async () => {
  const player = new Player();
  const json = fixture();
  json.song.source.offsetMs = 200;
  const clock = manualClockAdapter(60_000);
  await player.load(json, clock);

  assert.equal(player.position, 200); // 音源 0ms = 解析軸 200ms
  player.seek(1200);
  assert.equal(clock.getPositionMs(), 1000);
  assert.equal(player.position, 1200);
});

test("setOffset 後も position 軸で参照APIが機能する", async () => {
  const { player, clock } = await loadedPlayer();

  player.setOffset(1000); // 音源 0ms = 解析軸 1000ms
  assert.equal(player.position, 1000);
  assert.equal(player.currentPhrase(player.position).text, "夜に");

  player.seek(2500); // 解析軸 2500ms へ移動
  assert.equal(clock.getPositionMs(), 1500); // 音源側は offset 分引いた位置
  assert.equal(player.position, 2500);
  assert.equal(player.currentWord(player.position).text, "に");
  assert.equal(player.currentChar(player.position).char, "に");
});

test("applyOverrides で char タイミングを補正できる", async () => {
  const { player } = await loadedPlayer();
  // メリスマ補正の想定: 「夜」と「に」の境界を 2000ms → 2400ms へずらす
  player.applyOverrides([
    { path: [0, 0], endTime: 2400 },
    { path: [0, 0, 0], endTime: 2400 },
    { path: [0, 1], startTime: 2400 },
    { path: [0, 1, 0], startTime: 2400 },
  ]);
  assert.equal(player.currentWord(2200).text, "夜");
  assert.equal(player.currentChar(2200).char, "夜");

  player.applyOverrides([{ segment: 0, endTime: 3500 }]);
  assert.equal(player.findSegment(3700), null);
});

test("applyOverrides は不正なパスを拒否する", async () => {
  const { player } = await loadedPlayer();
  assert.throws(() => player.applyOverrides([{ path: [99], startTime: 0 }]));
});

test("再生中に load() を再呼び出しすると旧 tick が残らない", async () => {
  const player = new Player({ tickIntervalMs: 5 });
  await player.load(fixture(), manualClockAdapter(60_000));

  const positions = [];
  player.on("timeupdate", (ms) => positions.push(ms));

  player.play();
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(positions.length > 0); // 旧 tick が動作していることを確認

  await player.load(fixture(), manualClockAdapter(60_000));

  positions.length = 0;
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(positions, []); // load() で旧 tick は停止し、再生もしていない
});

test("timeupdate は tick で発火する", async () => {
  const { player, clock } = await loadedPlayer();
  const positions = [];
  player.on("timeupdate", (ms) => positions.push(ms));
  player.play();
  clock.advance(100);
  player.tick();
  clock.advance(100);
  player.tick();
  player.pause();
  assert.deepEqual(positions, [100, 200]);
});
