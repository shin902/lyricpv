/**
 * ブラウザ専用: OffscreenCanvas.captureStream() + MediaRecorder による
 * フレーム逐次書き出し (要件定義 11 章「MP4 書き出し」の MediaRecorder 経路)。
 *
 * captureStream() はキャンバスの内容を実時間ベースでサンプリングするため、
 * frame-driver.mjs の renderFrames() だけでは描画とサンプリングのタイミングが
 * 合わずフレームが欠落しうる。そのためフレームごとに fps 相当の実時間待機を挟み、
 * 描画内容がキャプチャに乗る時間を確保する(完全なフレーム精度が要る場合は
 * ffmpeg-export.mjs の経路を使うこと)。
 *
 * 出力は映像のみ (WebM)。音源のミキシングは ffmpeg-export.mjs の
 * muxVideoAudio() で後付けする。
 */

import { renderFrames } from "./frame-driver.mjs";

export class StreamExportError extends Error {}

/**
 * @param {import("./lyric-player.mjs").Player} player
 * @param {{seekTo: (ms: number) => void}} clock manualClockAdapter() の戻り値
 * @param {Object} options
 * @param {OffscreenCanvas|HTMLCanvasElement} options.canvas
 * @param {number} [options.fps=30]
 * @param {string} [options.mimeType]
 * @param {(frame: {index: number, ms: number, frameCount: number}) => (Promise<void>|void)} [options.onFrame]
 *   各フレームでキャンバスへ描画する処理(呼び出し側の責務)
 * @returns {Promise<Blob>} 書き出された WebM の Blob
 */
export function recordCanvasStream(player, clock, { canvas, fps = 30, mimeType, onFrame } = {}) {
  if (typeof MediaRecorder === "undefined") {
    throw new StreamExportError("この環境は MediaRecorder をサポートしていません");
  }
  if (typeof canvas?.captureStream !== "function") {
    throw new StreamExportError("canvas.captureStream() が利用できません (OffscreenCanvas 未対応の可能性)");
  }

  const stream = canvas.captureStream(fps);
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };

  const frameDurationMs = 1000 / fps;
  const pacedOnFrame = async (frame) => {
    if (onFrame) await onFrame(frame);
    await new Promise((resolve) => setTimeout(resolve, frameDurationMs));
  };

  return new Promise((resolve, reject) => {
    recorder.onerror = (event) => {
      reject(event.error ?? new StreamExportError("MediaRecorder でエラーが発生しました"));
    };
    recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType }));

    recorder.start();
    renderFrames(player, clock, { fps, onFrame: pacedOnFrame })
      .then(() => recorder.stop())
      .catch((err) => {
        recorder.stop();
        reject(err);
      });
  });
}
