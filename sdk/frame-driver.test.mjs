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
  assert.deepEqual(seen.map((f) => f.index), seen.map((_, i) => i));
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

test("renderFrames: clock に seekTo がなければ例外", async () => {
  const player = new Player();
  const clock = manualClockAdapter(100);
  await player.load(fixture(), clock);

  await assert.rejects(
    () => renderFrames(player, {}, { fps: 30, onFrame: () => {} }),
    /manualClockAdapter/
  );
});
