/**
 * フレーム書き出し用の環境非依存ドライバ。
 *
 * manualClockAdapter + Player.tick() を固定フレームレートで駆動し、
 * 各フレームのレンダリング・保存はコールバックに委ねる(SDK は描画を行わない)。
 * ブラウザの MediaRecorder 経路 (stream-export.mjs) と Node の ffmpeg 経路
 * (ffmpeg-export.mjs) の両方から共有される。
 */

/**
 * 0 から durationMs までを fps 刻みで分割したタイムスタンプ列を返す。
 * durationMs ちょうどのフレームも含む(末尾フレーム欠落を防ぐため)。
 *
 * @param {number} durationMs
 * @param {number} fps
 * @returns {number[]}
 */
export function frameTimestamps(durationMs, fps) {
  if (!(fps > 0)) throw new Error("fps は正の値で指定してください");
  const frameDurationMs = 1000 / fps;
  const totalFrames = Math.ceil(durationMs / frameDurationMs) + 1;
  const timestamps = [];
  for (let index = 0; index < totalFrames; index += 1) {
    timestamps.push(Math.min(Math.round(index * frameDurationMs), durationMs));
  }
  return timestamps;
}

/**
 * clock.seekTo(ms) + player.tick() でフレームを 1 枚ずつ確定させ、
 * 都度 onFrame を await する。実時間とは無関係に進むため、
 * 描画やファイル書き出しが重くてもフレーム抜け・音ズレは発生しない。
 *
 * @param {import("./lyric-player.mjs").Player} player
 * @param {{seekTo: (ms: number) => void}} clock manualClockAdapter() の戻り値
 * @param {Object} options
 * @param {number} options.fps
 * @param {(frame: {index: number, ms: number, frameCount: number}) => (Promise<void>|void)} options.onFrame
 * @returns {Promise<number>} 書き出したフレーム数
 */
export async function renderFrames(player, clock, { fps, onFrame }) {
  if (typeof clock.seekTo !== "function") {
    throw new Error("clock は manualClockAdapter() の戻り値を渡してください");
  }
  const timestamps = frameTimestamps(player.songDurationMs, fps);
  for (let index = 0; index < timestamps.length; index += 1) {
    clock.seekTo(timestamps[index]);
    player.tick();
    await onFrame({ index, ms: player.position, frameCount: timestamps.length });
  }
  return timestamps.length;
}
