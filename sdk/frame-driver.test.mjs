/**
 * frame-driver.mjs のテスト (node --test sdk/ で実行)。
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { frameTimestamps, renderFrames } from "./frame-driver.mjs";
import { Player, manualClockAdapter } from "./lyric-player.mjs";

function fixture() {
  return {
    song: {
      title: "テスト曲",
      artist: "テスト歌手",
      durationMs: 100,
      source: { type: "file", id: "test.wav", offsetMs: 0 },
    },
    phrases: [],
  };
}

test("frameTimestamps: durationMs ちょうどの末尾フレームを含む", () => {
  const timestamps = frameTimestamps(100, 30);
  assert.equal(timestamps[0], 0);
  assert.equal(timestamps.at(-1), 100);
  assert.ok(timestamps.every((ms, i) => i === 0 || ms >= timestamps[i - 1]));
});

test("frameTimestamps: fps が不正なら例外", () => {
  assert.throws(() => frameTimestamps(100, 0));
  assert.throws(() => frameTimestamps(100, -1));
});

test("frameTimestamps: 浮動小数点誤差で末尾フレームが重複しない", () => {
  // durationMs/frameDurationMs が割り切れず round() が durationMs に複数回到達しうる組み合わせ
  for (const [durationMs, fps] of [
    [667, 3],
    [167, 6],
    [143, 7],
    [100, 30],
  ]) {
    const timestamps = frameTimestamps(durationMs, fps);
    assert.equal(timestamps.at(-1), durationMs);
    assert.notEqual(
      timestamps.at(-2),
      timestamps.at(-1),
      `dup at duration=${durationMs} fps=${fps}`,
    );
  }
});

test("renderFrames: 各フレームで clock を seek し timeupdate 順に onFrame が呼ばれる", async () => {
  const player = new Player();
  const clock = manualClockAdapter(100);
  await player.load(fixture(), clock);

  const seen = [];
  const frameCount = await renderFrames(player, clock, {
    fps: 30,
    onFrame: (frame) => {
      seen.push(frame);
    },
  });

  assert.equal(frameCount, seen.length);
  assert.equal(seen[0].ms, 0);
  assert.equal(seen.at(-1).ms, 100);
  assert.deepEqual(
    seen.map((f) => f.index),
    seen.map((_, i) => i),
  );
  for (let i = 1; i < seen.length; i += 1) {
    assert.ok(seen[i].ms >= seen[i - 1].ms, "ms は単調増加であること");
  }
});

test("renderFrames: onFrame の await を待ってから次のフレームへ進む", async () => {
  const player = new Player();
  const clock = manualClockAdapter(60);
  await player.load(fixture(), clock);

  let running = 0;
  let maxConcurrent = 0;
  await renderFrames(player, clock, {
    fps: 30,
    onFrame: async () => {
      running += 1;
      maxConcurrent = Math.max(maxConcurrent, running);
      await new Promise((resolve) => setTimeout(resolve, 0));
      running -= 1;
    },
  });

  assert.equal(maxConcurrent, 1);
});

test("renderFrames: offsetMs があっても timeupdate と onFrame の時刻が一致する", async () => {
  const player = new Player();
  const json = fixture();
  json.song.source.offsetMs = 20;
  const clock = manualClockAdapter(100);
  await player.load(json, clock);

  const updates = [];
  const frames = [];
  player.on("timeupdate", (ms) => updates.push(ms));
  await renderFrames(player, clock, {
    fps: 10,
    onFrame: ({ ms }) => frames.push(ms),
  });

  assert.deepEqual(updates, frames);
  assert.deepEqual(frames, [0, 100]);
  assert.equal(clock.getPositionMs(), 80);
});

test("renderFrames: setOffset() 後も timeupdate と onFrame の時刻が一致する", async () => {
  const player = new Player();
  const clock = manualClockAdapter(100);
  await player.load(fixture(), clock);
  player.setOffset(-20);

  const updates = [];
  const frames = [];
  player.on("timeupdate", (ms) => updates.push(ms));
  await renderFrames(player, clock, {
    fps: 10,
    onFrame: ({ ms }) => frames.push(ms),
  });

  assert.deepEqual(updates, frames);
  assert.deepEqual(frames, [0, 100]);
  assert.equal(clock.getPositionMs(), 120);
});

test("renderFrames: clock に seekTo がなければ例外", async () => {
  const player = new Player();
  const clock = manualClockAdapter(100);
  await player.load(fixture(), clock);

  await assert.rejects(
    () => renderFrames(player, {}, { fps: 30, onFrame: () => {} }),
    /manualClockAdapter/,
  );
});
