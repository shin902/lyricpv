/**
 * lyric-player — 契約B: 文字PV生成のためのランタイム SDK。
 *
 * TextAlive App API のインターフェースを踏襲し、描画は一切行わない。
 * 契約A の JSON (オフライン解析の出力) を読み込み、音源と同期した
 * 「いつ・何が・どのくらいの強さで」のデータ供給に徹する。
 * アニメーションは利用者が p5.js / three.js / canvas 等で自由に実装する。
 *
 * 依存ゼロの ES module。ブラウザでも Node でも動く。
 *
 * @example
 * import { Player, htmlAudioAdapter } from "./lyric-player.mjs";
 * const player = new Player();
 * await player.load(json, htmlAudioAdapter(document.querySelector("audio")));
 * player.on("timeupdate", (ms) => {
 *   const char = player.currentChar(ms);   // 今発声中の文字
 *   const beat = player.findBeat(ms);      // 直近のビート
 *   const amp  = player.getVocalAmplitude(ms); // 声量 0–1
 *   // → 利用者のレンダラがこれらを絵にする
 * });
 * player.play();
 */

/**
 * 音源アダプタのインターフェース。
 * HTMLAudioElement・YouTube IFrame Player・手動クロック等をこの形に包む。
 *
 * @typedef {Object} AudioAdapter
 * @property {() => void} play
 * @property {() => void} pause
 * @property {(ms: number) => void} seekTo
 * @property {() => number} getPositionMs
 * @property {(cb: () => void) => void} [onEnded] 再生終了通知 (任意)
 */

/** HTMLAudioElement を AudioAdapter に包む。 */
export function htmlAudioAdapter(audioEl) {
  // リスナーは 1 本だけ張り、onEnded はコールバックの差し替えにする
  // (load() を再呼び出ししても end イベントが多重発火しない)
  let endedCb = null;
  audioEl.addEventListener("ended", () => {
    if (endedCb) endedCb();
  });
  return {
    play: () => audioEl.play(),
    pause: () => audioEl.pause(),
    seekTo: (ms) => {
      audioEl.currentTime = ms / 1000;
    },
    getPositionMs: () => audioEl.currentTime * 1000,
    onEnded: (cb) => {
      endedCb = cb;
    },
  };
}

/**
 * 手動クロックの AudioAdapter。実音源なしの動作確認・テスト・
 * フレーム逐次レンダリング (MP4 書き出し) 用。
 * `advance(ms)` で時間を進める。
 */
export function manualClockAdapter(durationMs) {
  let pos = 0;
  let playing = false;
  let endedCb = null;
  return {
    play: () => {
      playing = true;
    },
    pause: () => {
      playing = false;
    },
    seekTo: (ms) => {
      // Player.seek() は解析軸の offsetMs を差し引いた値を渡すため、
      // フレーム書き出しでは負値や durationMs 超過も保持する必要がある。
      // advance() による通常の手動再生は従来どおり曲末で停止する。
      pos = ms;
    },
    getPositionMs: () => pos,
    onEnded: (cb) => {
      endedCb = cb;
    },
    /** テスト・レンダラ駆動用: 再生中なら ms だけ時間を進める。 */
    advance(ms) {
      if (!playing) return;
      pos += ms;
      if (pos >= durationMs) {
        pos = durationMs;
        playing = false;
        if (endedCb) endedCb();
      }
    },
    get playing() {
      return playing;
    },
  };
}

/** startTime 昇順の配列から、startTime <= ms を満たす最後の要素番号を返す (なければ -1)。 */
function bisectLast(items, ms, key = "startTime") {
  let lo = 0;
  let hi = items.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid][key] <= ms) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/** 区間リスト (startTime/endTime) から ms を含む要素を返す。 */
function findContaining(items, ms) {
  const i = bisectLast(items, ms);
  if (i < 0) return null;
  const item = items[i];
  return ms < item.endTime ? item : null;
}

export class Player {
  /**
   * @param {Object} [options]
   * @param {number} [options.tickIntervalMs=16] timeupdate の発火間隔
   */
  constructor(options = {}) {
    this._tickIntervalMs = options.tickIntervalMs ?? 16;
    this._listeners = new Map(); // event -> Set<cb>
    this._data = null;
    this._audio = null;
    this._offsetMs = 0;
    this._timer = null;
    this._flatWords = [];
    this._flatChars = [];
  }

  // ----- ライフサイクル -----------------------------------------------

  /**
   * 契約A の JSON と音源アダプタを読み込む。
   * 渡された json は変更せず、Player は専有のディープコピーを保持する。
   * @param {Object} json 契約A (TextAlive 互換 JSON)
   * @param {AudioAdapter} audio
   */
  async load(json, audio) {
    if (!json || !json.song || !Array.isArray(json.phrases)) {
      throw new Error("契約Aの形式ではありません: song / phrases がありません");
    }
    this._stopTicking();
    this._audio?.pause(); // 再生中の再load: 旧アダプタを止める
    this._data = structuredClone(json);
    this._audio = audio;
    this._offsetMs = json.song.source?.offsetMs ?? 0;
    this._rebuildIndex();
    if (audio.onEnded) {
      audio.onEnded(() => {
        if (this._audio !== audio) return; // 別アダプタへ再ロード済みなら旧アダプタの通知は無視
        this._stopTicking();
        this._emit("end");
      });
    }
    this._emit("ready");
  }

  /** @param {"ready"|"timeupdate"|"play"|"pause"|"end"} event */
  on(event, cb) {
    if (!this._listeners.has(event)) this._listeners.set(event, new Set());
    this._listeners.get(event).add(cb);
  }

  off(event, cb) {
    this._listeners.get(event)?.delete(cb);
  }

  play() {
    this._requireLoaded();
    this._audio.play();
    this._startTicking();
    this._emit("play");
  }

  pause() {
    this._requireLoaded();
    this._audio.pause();
    this._stopTicking();
    this._emit("pause");
  }

  /** @param {number} ms 解析音源基準の時刻 */
  seek(ms) {
    this._requireLoaded();
    this._audio.seekTo(ms - this._offsetMs);
    this._emit("timeupdate", this.position);
  }

  /** 現在の再生位置 [ms] (offsetMs 補正込み・解析データと同一の時間軸)。 */
  get position() {
    this._requireLoaded();
    return this._audio.getPositionMs() + this._offsetMs;
  }

  /**
   * timeupdate を 1 回発火させる。requestAnimationFrame 等で
   * 利用者が自前駆動する場合 (MP4 書き出し等) に使う。
   */
  tick() {
    this._emit("timeupdate", this.position);
  }

  // ----- 楽曲地図の参照 -------------------------------------------------

  /** ms 時点で鳴っているビート (直近の拍)。 */
  findBeat(ms) {
    const beats = this._data?.beats ?? [];
    const i = bisectLast(beats, ms);
    return i >= 0 ? beats[i] : null;
  }

  /** ms 時点がサビ区間ならその Segment。 */
  findChorus(ms) {
    const seg = findContaining(this._data?.segments ?? [], ms);
    return seg && seg.label === "chorus" ? seg : null;
  }

  /** ms 時点のコード。 */
  findChord(ms) {
    return findContaining(this._data?.chords ?? [], ms);
  }

  /** ms 時点の構造セグメント (intro/verse/chorus/outro 等)。 */
  findSegment(ms) {
    return findContaining(this._data?.segments ?? [], ms);
  }

  // ----- 発声中の歌詞 ---------------------------------------------------

  currentPhrase(ms) {
    return findContaining(this._data?.phrases ?? [], ms);
  }

  currentWord(ms) {
    return findContaining(this._flatWords, ms);
  }

  currentChar(ms) {
    return findContaining(this._flatChars, ms);
  }

  // ----- 表現用パラメータ -----------------------------------------------

  /** ms 時点の声量 (0–1)。点の間は線形補間。 */
  getVocalAmplitude(ms) {
    const points = this._data?.amplitude ?? [];
    if (points.length === 0) return 0;
    const i = bisectLast(points, ms, "time");
    if (i < 0) return 0;
    if (i >= points.length - 1) return points[points.length - 1].value;
    const a = points[i];
    const b = points[i + 1];
    const t = (ms - a.time) / (b.time - a.time || 1);
    return a.value + (b.value - a.value) * t;
  }

  /** ms 時点のムード (valence/arousal、-1〜1)。 */
  getValenceArousal(ms) {
    const points = this._data?.valenceArousal ?? [];
    const i = bisectLast(points, ms, "time");
    if (i < 0) return { valence: 0, arousal: 0 };
    return { valence: points[i].valence, arousal: points[i].arousal };
  }

  // ----- 手動上書きフック -----------------------------------------------

  /**
   * 自動生成タイミングの手動補正 (要件定義 8 章・9 章)。
   * メリスマ等で按分がずれた word/char を「叩き台から nudge」するための API。
   *
   * @param {Array<Object>} overrides 各要素:
   *   - `{ path: [phraseIdx], startTime?, endTime? }`            … phrase の補正
   *   - `{ path: [phraseIdx, wordIdx], startTime?, endTime? }`   … word の補正
   *   - `{ path: [phraseIdx, wordIdx, charIdx], startTime?, endTime? }` … char の補正
   *   - `{ segment: segIdx, startTime?, endTime? }`              … segment 境界の補正
   *
   * 適用後は phrases / segments が startTime 昇順に再ソートされる。
   * 順序が入れ替わる上書きをした場合、以降の path のインデックスは
   * ソート後の `player.data` を参照して指定すること。
   */
  applyOverrides(overrides) {
    this._requireLoaded();
    for (const o of overrides) {
      let target = null;
      if (Array.isArray(o.path)) {
        const [pi, wi, ci] = o.path;
        target = this._data.phrases[pi];
        if (wi !== undefined) target = target?.words?.[wi];
        if (ci !== undefined) target = target?.chars?.[ci];
      } else if (o.segment !== undefined) {
        target = this._data.segments[o.segment];
      }
      if (!target) {
        throw new Error(`上書き対象が見つかりません: ${JSON.stringify(o)}`);
      }
      if (o.startTime !== undefined) target.startTime = o.startTime;
      if (o.endTime !== undefined) target.endTime = o.endTime;
    }
    this._rebuildIndex();
  }

  /** 音源と解析データの頭ズレ補正値を設定する (ライブ校正用)。 */
  setOffset(ms) {
    this._offsetMs = ms;
  }

  get offsetMs() {
    return this._offsetMs;
  }

  /** 読み込み済みの契約A データ (読み取り用)。 */
  get data() {
    return this._data;
  }

  get songDurationMs() {
    return this._data?.song?.durationMs ?? 0;
  }

  // ----- 内部 -----------------------------------------------------------

  _rebuildIndex() {
    // currentPhrase / findChorus 等は startTime 昇順前提の二分探索なので、
    // 上書きで順序が入れ替わった phrases / segments も再ソートする
    this._data.phrases.sort((a, b) => a.startTime - b.startTime);
    if (Array.isArray(this._data.segments)) {
      this._data.segments.sort((a, b) => a.startTime - b.startTime);
    }
    const words = [];
    const chars = [];
    for (const p of this._data.phrases) {
      for (const w of p.words ?? []) {
        words.push(w);
        for (const c of w.chars ?? []) chars.push(c);
      }
    }
    words.sort((a, b) => a.startTime - b.startTime);
    chars.sort((a, b) => a.startTime - b.startTime);
    this._flatWords = words;
    this._flatChars = chars;
  }

  _startTicking() {
    if (this._timer !== null) return;
    this._timer = setInterval(() => this.tick(), this._tickIntervalMs);
  }

  _stopTicking() {
    if (this._timer !== null) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  _requireLoaded() {
    if (!this._data || !this._audio) {
      throw new Error("load() を先に呼んでください");
    }
  }

  _emit(event, ...args) {
    for (const cb of this._listeners.get(event) ?? []) cb(...args);
  }
}
