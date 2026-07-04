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
  await assert.rejects(
    () => recordCanvasStream(player, clock, { canvas: { captureStream: () => ({}) } }),
    StreamExportError
  );
});

test("recordCanvasStream: canvas.captureStream が無い場合は StreamExportError", async () => {
  const original = globalThis.MediaRecorder;
  globalThis.MediaRecorder = class {};
  try {
    const { player, clock } = await loadedPlayer();
    await assert.rejects(
      () => recordCanvasStream(player, clock, { canvas: {} }),
      StreamExportError
    );
  } finally {
    if (original === undefined) {
      delete globalThis.MediaRecorder;
    } else {
      globalThis.MediaRecorder = original;
    }
  }
});

test("recordCanvasStream: canvas が未指定の場合も StreamExportError(captureStream 欠落として扱う)", async () => {
  const original = globalThis.MediaRecorder;
  globalThis.MediaRecorder = class {};
  try {
    const { player, clock } = await loadedPlayer();
    await assert.rejects(
      () => recordCanvasStream(player, clock, {}),
      StreamExportError
    );
  } finally {
    if (original === undefined) {
      delete globalThis.MediaRecorder;
    } else {
      globalThis.MediaRecorder = original;
    }
  }
});

test("recordCanvasStream: MediaRecorder 構築失敗時はトラックを停止して rejected Promise を返す", async () => {
  const original = globalThis.MediaRecorder;
  globalThis.MediaRecorder = class {
    constructor() {
      throw new Error("unsupported mimeType");
    }
  };
  let stopped = false;
  const canvas = {
    captureStream: () => ({
      getTracks: () => [{ stop: () => { stopped = true; } }],
    }),
  };
  try {
    const { player, clock } = await loadedPlayer();
    let result;
    assert.doesNotThrow(() => {
      result = recordCanvasStream(player, clock, {
        canvas,
        mimeType: "video/unsupported",
      });
    });
    await assert.rejects(result, /unsupported mimeType/);
    assert.equal(stopped, true);
  } finally {
    if (original === undefined) {
      delete globalThis.MediaRecorder;
    } else {
      globalThis.MediaRecorder = original;
    }
  }
});
