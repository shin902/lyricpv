/**
 * ffmpeg-export.mjs の引数検証のテスト (node --test sdk/ で実行)。
 * 実際の ffmpeg 起動を伴うテストはここでは行わない(CI に依存を持たせないため)。
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { FfmpegExportError, muxFramesToMp4, muxVideoAudio } from "./ffmpeg-export.mjs";

test("muxFramesToMp4: '-' で始まる引数は引数インジェクションとして拒否する", async () => {
  await assert.rejects(
    () => muxFramesToMp4({ framePattern: "frame-%05d.png", fps: 30, audioPath: "a.wav", outPath: "-rf" }),
    FfmpegExportError
  );
});

test("muxFramesToMp4: 空文字列の引数は拒否する", async () => {
  await assert.rejects(
    () => muxFramesToMp4({ framePattern: "", fps: 30, audioPath: "a.wav", outPath: "out.mp4" }),
    FfmpegExportError
  );
});

test("muxVideoAudio: ffmpegPath が '-' で始まる場合は拒否する", async () => {
  await assert.rejects(
    () => muxVideoAudio({ videoPath: "v.webm", audioPath: "a.wav", outPath: "out.mp4", ffmpegPath: "--evil" }),
    FfmpegExportError
  );
});
