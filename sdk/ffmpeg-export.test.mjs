/**
 * ffmpeg-export.mjs のテスト (node --test sdk/ で実行)。
 * 実 ffmpeg は使わず、引数を記録するだけのスタブスクリプトを ffmpegPath に渡すことで、
 * 実バイナリへの依存なしに spawn 呼び出しの組み立て・終了コード処理を検証する。
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, writeFile, readFile, rm, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { FfmpegExportError, muxFramesToMp4, muxVideoAudio, exportFramesToMp4 } from "./ffmpeg-export.mjs";
import { Player, manualClockAdapter } from "./lyric-player.mjs";

/** 受け取った argv を JSON でファイルに書き出すだけのスタブ "ffmpeg"。 */
async function makeStubFfmpeg(dir, { exitCode = 0 } = {}) {
  const scriptPath = join(dir, "stub-ffmpeg.sh");
  const argsPath = join(dir, "args.json");
  const script = `#!/bin/sh
printf '%s\\n' "$@" > "${argsPath}"
exit ${exitCode}
`;
  await writeFile(scriptPath, script);
  await chmod(scriptPath, 0o755);
  return {
    scriptPath,
    readArgs: async () => (await readFile(argsPath, "utf8")).split("\n").filter((s) => s.length > 0),
  };
}

function fixture(durationMs) {
  return {
    song: { title: "t", artist: "a", durationMs, source: { type: "file", id: "t.wav", offsetMs: 0 } },
    phrases: [],
  };
}

test("muxFramesToMp4: 成功時は ffmpeg に framerate/入力/出力を渡す", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ffmpeg-export-test-"));
  try {
    const stub = await makeStubFfmpeg(dir);
    await muxFramesToMp4({
      framePattern: "frames/frame-%05d.png",
      fps: 24,
      audioPath: "master.wav",
      outPath: "out.mp4",
      ffmpegPath: stub.scriptPath,
    });
    const args = await stub.readArgs();
    assert.ok(args.includes("-framerate"));
    assert.ok(args.includes("24"));
    assert.ok(args.includes("frames/frame-%05d.png"));
    assert.ok(args.includes("master.wav"));
    assert.ok(args.includes("out.mp4"));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("muxFramesToMp4: ffmpeg が非0終了したら FfmpegExportError を投げる", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ffmpeg-export-test-"));
  try {
    const stub = await makeStubFfmpeg(dir, { exitCode: 1 });
    await assert.rejects(
      () => muxFramesToMp4({
        framePattern: "frames/frame-%05d.png",
        fps: 24,
        audioPath: "master.wav",
        outPath: "out.mp4",
        ffmpegPath: stub.scriptPath,
      }),
      FfmpegExportError
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("exportFramesToMp4: renderFrames で writeFrame を全フレーム呼び、その後 muxFramesToMp4 相当の ffmpeg 呼び出しを行う", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ffmpeg-export-test-"));
  try {
    const stub = await makeStubFfmpeg(dir);
    const player = new Player();
    const clock = manualClockAdapter(100);
    await player.load(fixture(100), clock);

    const written = [];
    const frameCount = await exportFramesToMp4(player, clock, {
      fps: 30,
      framePattern: join(dir, "frame-%05d.png"),
      audioPath: "master.wav",
      outPath: "out.mp4",
      ffmpegPath: stub.scriptPath,
      writeFrame: (frame) => {
        written.push(frame);
      },
    });

    assert.equal(frameCount, written.length);
    assert.ok(frameCount > 1);
    assert.equal(written[0].ms, 0);
    assert.equal(written.at(-1).ms, 100);

    const args = await stub.readArgs();
    assert.ok(args.includes("master.wav"));
    assert.ok(args.includes("out.mp4"));
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

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
