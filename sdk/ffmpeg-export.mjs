/**
 * Node 専用: フレーム連番画像 + 音源を ffmpeg で MP4 に合成する書き出し経路。
 * (要件定義 11 章「MP4 書き出し」/ docs/architecture.md の ffmpeg パイプライン経路)
 *
 * フレームの描画自体はこのモジュールの責務ではない。`exportFramesToMp4()` の
 * `writeFrame` に、Canvas 等から得たピクセルデータをファイルへ保存する処理を渡すこと。
 * 実時間に依存しない frame-driver.mjs の renderFrames() で駆動するため、
 * レンダリングが重くてもフレーム抜け・音ズレは発生しない。
 *
 * ffmpeg / ffprobe バイナリが PATH にあることを前提とする(本リポジトリの
 * src/lyricpv/fetch.py と同じ前提)。
 */

import { spawn } from "node:child_process";

import { renderFrames } from "./frame-driver.mjs";

export class FfmpegExportError extends Error {}

/**
 * ffmpeg に渡す文字列引数(パス・パターン)を検証する。
 * spawn() は配列引数のためシェルインジェクションは起きないが、
 * "-" で始まる値は ffmpeg にオプションとして解釈されうる(引数インジェクション)。
 */
function assertSafeArg(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new FfmpegExportError(`${label} は空でない文字列を指定してください`);
  }
  if (value.startsWith("-")) {
    throw new FfmpegExportError(`${label} に "-" で始まる値は指定できません (ffmpeg オプションとして解釈される可能性があります): ${value}`);
  }
}

/** エラーメッセージに含める stderr の末尾保持量。 */
const STDERR_TAIL_LIMIT = 4000;

function runFfmpeg(args, { ffmpegPath = "ffmpeg", timeoutMs = 10 * 60 * 1000 } = {}) {
  assertSafeArg(ffmpegPath, "ffmpegPath");
  return new Promise((resolve, reject) => {
    const proc = spawn(ffmpegPath, args);
    proc.stdout.resume();
    let stderr = "";
    let settled = false;
    const settle = (fn) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      proc.stderr.removeAllListeners("data");
      fn();
    };
    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      settle(() => reject(new FfmpegExportError(`ffmpeg がタイムアウトしました (${timeoutMs}ms)`)));
    }, timeoutMs);
    proc.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      // 長時間のエンコードで進捗ログが大量に出ても無制限に蓄積しないよう、末尾のみ保持する
      if (stderr.length > STDERR_TAIL_LIMIT) {
        stderr = stderr.slice(-STDERR_TAIL_LIMIT);
      }
    });
    proc.on("error", (err) => {
      settle(() => reject(new FfmpegExportError(`ffmpeg の起動に失敗しました: ${err.message}`)));
    });
    proc.on("close", (code) => {
      settle(() => {
        if (code === 0) {
          resolve();
        } else {
          reject(new FfmpegExportError(`ffmpeg が失敗しました (code=${code}): ${stderr.slice(-500)}`));
        }
      });
    });
  });
}

/**
 * 連番フレーム画像と音源を ffmpeg で 1 本の MP4 に合成する。
 *
 * @param {Object} options
 * @param {string} options.framePattern ffmpeg の連番指定 (例: "frames/frame-%05d.png")
 * @param {number} options.fps
 * @param {string} options.audioPath 音源ファイル (例: master.wav)
 * @param {string} options.outPath 出力先 MP4 パス
 * @param {string} [options.ffmpegPath="ffmpeg"]
 * @param {number} [options.timeoutMs]
 */
export async function muxFramesToMp4({ framePattern, fps, audioPath, outPath, ffmpegPath, timeoutMs }) {
  assertSafeArg(framePattern, "framePattern");
  assertSafeArg(audioPath, "audioPath");
  assertSafeArg(outPath, "outPath");
  if (!Number.isFinite(fps) || fps <= 0) {
    throw new FfmpegExportError("fps は正の有限数を指定してください");
  }
  const args = [
    "-y",
    "-framerate", String(fps),
    "-i", framePattern,
    "-i", audioPath,
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-shortest",
    "-movflags", "+faststart",
    outPath,
  ];
  await runFfmpeg(args, { ffmpegPath, timeoutMs });
}

/**
 * 既存の動画ファイル(stream-export.mjs の MediaRecorder 経路で得た WebM 等、
 * 映像のみ)に音源を後付けでミキシングする。
 *
 * @param {Object} options
 * @param {string} options.videoPath
 * @param {string} options.audioPath
 * @param {string} options.outPath
 * @param {string} [options.ffmpegPath="ffmpeg"]
 * @param {number} [options.timeoutMs]
 */
export async function muxVideoAudio({ videoPath, audioPath, outPath, ffmpegPath, timeoutMs }) {
  assertSafeArg(videoPath, "videoPath");
  assertSafeArg(audioPath, "audioPath");
  assertSafeArg(outPath, "outPath");
  const args = [
    "-y",
    "-i", videoPath,
    "-i", audioPath,
    "-c:v", "copy",
    "-c:a", "aac",
    "-shortest",
    outPath,
  ];
  await runFfmpeg(args, { ffmpegPath, timeoutMs });
}

/**
 * Player を固定フレームレートで駆動し、各フレームを `writeFrame` で保存した後、
 * ffmpeg で音源とともに MP4 へ合成する。
 *
 * @param {import("./lyric-player.mjs").Player} player
 * @param {{seekTo: (ms: number) => void}} clock manualClockAdapter() の戻り値
 * @param {Object} options
 * @param {number} options.fps
 * @param {string} options.framePattern muxFramesToMp4 に渡す連番パターン
 * @param {(frame: {index: number, ms: number, frameCount: number}) => (Promise<void>|void)} options.writeFrame
 *   各フレームのピクセルデータを framePattern に対応するファイルへ保存する処理
 * @param {string} options.audioPath
 * @param {string} options.outPath
 * @param {string} [options.ffmpegPath]
 * @param {number} [options.timeoutMs]
 * @returns {Promise<number>} 書き出したフレーム数
 */
export async function exportFramesToMp4(player, clock, { fps, framePattern, writeFrame, audioPath, outPath, ffmpegPath, timeoutMs }) {
  const frameCount = await renderFrames(player, clock, { fps, onFrame: writeFrame });
  await muxFramesToMp4({ framePattern, fps, audioPath, outPath, ffmpegPath, timeoutMs });
  return frameCount;
}
