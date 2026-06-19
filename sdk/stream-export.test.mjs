/**
 * stream-export.mjs のテスト (node --test sdk/ で実行)。
 * MediaRecorder / captureStream はブラウザ専用 API のため、
 * 実録画は対象外。Node でも検証できる入力バリデーションのみテストする。
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { recordCanvasStream, StreamExportError } from "./stream-export.mjs";
import { Player, manualClockAdapter } from "./lyric-player.mjs";

function fixture() {
  return {
    song: { title: "t", artist: "a", durationMs: 100, source: { type: "file", id: "t.wav", offsetMs: 0 } },
    phrases: [],
  };
}

async function loadedPlayer() {
  const player = new Player();
  const clock = manualClockAdapter(100);
  await player.load(fixture(), clock);
  return { player, clock };
}

test("recordCanvasStream: MediaRecorder 未定義の環境では StreamExportError", async () => {
  assert.equal(typeof globalThis.MediaRecorder, "undefined");
  const { player, clock } = await loadedPlayer();
  assert.throws(
    () => recordCanvasStream(player, clock, { canvas: { captureStream: () => ({}) } }),
    StreamExportError
  );
});

test("recordCanvasStream: canvas.captureStream が無い場合は StreamExportError", async () => {
  globalThis.MediaRecorder = class {};
  try {
    const { player, clock } = await loadedPlayer();
    assert.throws(
      () => recordCanvasStream(player, clock, { canvas: {} }),
      StreamExportError
    );
  } finally {
    delete globalThis.MediaRecorder;
  }
});

test("recordCanvasStream: canvas が未指定の場合も StreamExportError(captureStream 欠落として扱う)", async () => {
  globalThis.MediaRecorder = class {};
  try {
    const { player, clock } = await loadedPlayer();
    assert.throws(
      () => recordCanvasStream(player, clock, {}),
      StreamExportError
    );
  } finally {
    delete globalThis.MediaRecorder;
  }
});
