/**
 * ブラウザ専用: HTMLCanvasElement.captureStream() + MediaRecorder による
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
 * @param {HTMLCanvasElement} options.canvas
 * @param {number} [options.fps=30]
 * @param {string} [options.mimeType]
 * @param {(frame: {index: number, ms: number, frameCount: number}) => (Promise<void>|void)} [options.onFrame]
 *   各フレームでキャンバスへ描画する処理(呼び出し側の責務)
 * @returns {Promise<Blob>} 書き出された WebM の Blob
 */
export function recordCanvasStream(player, clock, { canvas, fps = 30, mimeType, onFrame } = {}) {
  if (typeof MediaRecorder === "undefined") {
    return Promise.reject(new StreamExportError("この環境は MediaRecorder をサポートしていません"));
  }
  if (typeof canvas?.captureStream !== "function") {
    return Promise.reject(
      new StreamExportError(
        "canvas.captureStream() が利用できません (OffscreenCanvas 未対応の可能性)",
      ),
    );
  }

  let stream;
  let recorder;
  try {
    stream = canvas.captureStream(fps);
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  } catch (err) {
    // MediaRecorder のコンストラクタは未対応 mimeType などで同期的に失敗する。
    // captureStream() 済みならトラックを止め、API 契約どおり rejected Promise を返す。
    stream?.getTracks().forEach((track) => {
      track.stop();
    });
    return Promise.reject(err);
  }
  const chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };

  const frameDurationMs = 1000 / fps;
  const pacedOnFrame = async (frame) => {
    const start = Date.now();
    if (onFrame) await onFrame(frame);
    const elapsed = Date.now() - start;
    const remaining = frameDurationMs - elapsed;
    if (remaining > 0) {
      await new Promise((resolve) => setTimeout(resolve, remaining));
    }
  };

  const cleanupStream = () => {
    stream.getTracks().forEach((t) => {
      t.stop();
    });
  };
  const stopRecorder = () => {
    if (recorder.state !== "inactive") recorder.stop();
  };

  return new Promise((resolve, reject) => {
    let settled = false;
    const settle = (fn) => {
      if (settled) return;
      settled = true;
      fn();
    };

    recorder.onerror = (event) => {
      settle(() =>
        reject(event.error ?? new StreamExportError("MediaRecorder でエラーが発生しました")),
      );
      stopRecorder();
      cleanupStream();
    };
    recorder.onstop = () => {
      cleanupStream();
      settle(() => resolve(new Blob(chunks, { type: recorder.mimeType })));
    };

    try {
      recorder.start();
    } catch (err) {
      cleanupStream();
      settle(() => reject(err));
      return;
    }
    renderFrames(player, clock, { fps, onFrame: pacedOnFrame })
      .then(() => stopRecorder())
      .catch((err) => {
        settle(() => reject(err));
        stopRecorder();
        cleanupStream();
      });
  });
}
