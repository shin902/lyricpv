# 文字PV生成 SDK ― 要件定義 / 全体方針

> 任意の楽曲（YouTube 等の自由曲）に対して、TextAlive App API 相当の解析データを供給し、プログラムから二次創作的な文字PV（キネティックタイポグラフィMV）を生成できるようにするための SDK。

---

## 1. 背景・目的

TextAlive App API は「歌詞のタイミング情報」「楽曲地図（ビート/コード/サビ）」「声量」「ムード（V/A）」といった豊富な解析データを供給し、描画は開発者が自由に書ける、という優れた設計を持つ。ただしこれらのデータは TextAlive/Songle のバックエンドが事前解析した**登録曲にしか存在しない**ため、自由曲では使えない。

本プロジェクトは、この解析バックエンドを OSS で自前再現し、**任意の曲で TextAlive App API 相当のデータを得られるようにする**ことを目的とする。最終的なユースケースは、プログラムからの文字PV（二次創作リリックビデオ）生成。

### 設計思想

TextAlive App API と同じく、**SDK はデータとライフサイクルの供給に徹し、描画は行わない**。アニメーションは開発者が好きなライブラリ（p5.js / three.js / canvas 等）で実装する。

---

## 2. スコープ

### 含むもの
- 楽曲の音響解析（ビート・ダウンビート・構造・コード・声量・ムード）
- 歌詞の取得とタイミング付与（フレーズ→単語→文字の階層）
- 解析結果の TextAlive 互換 JSON への正規化
- JSON を読み込み音源と同期するランタイム SDK（JS/TS）
- 日本語・ボカロ楽曲への対応

### 含まないもの
- 描画・アニメーション本体（開発者の責務）
- 楽曲・歌詞そのものの再配布
- TextAlive 固有のホスト連携 / Songle Sync 相当

### 著作権の前提
- **インタラクティブ Web アプリ出力**：音源は YouTube IFrame Player 等からストリーム再生し、ファイルを再配布しない構成とする（最もクリーン）。
- **MP4 書き出し出力**：piapro ライセンス等で二次利用可の楽曲を選び、ニコニコ/YouTube のプラットフォーム包括契約の範囲でアップロードする前提。
- ダウンロードした解析用音源は解析処理内に留め、配布物に含めない。

---

## 3. アーキテクチャ全体像

処理は「重くて正確なオフライン世界（Python）」と「軽くて速いオンライン世界（JS）」の2つに分かれ、その間を **2つの契約（contract）** が繋ぐ。

```
[オフライン / Python / 1曲につき1回]
  ① 取得      yt-dlp → WAVマスター / 歌詞フェッチ
  ② 分離      RoFormer or htdemucs_ft（ボーカル/伴奏）
  ③ 楽曲地図   allin1（tempo/beat/downbeat/構造）+ コード + 声量 + V/A
  ④ 歌詞整合   既知歌詞 × ボーカル → phrase/word/char に時刻付与
  ⑤ 規格化     === TextAlive 互換 JSON ===      ← 契約A（オフライン↔オンライン）

[オンライン / JS・TS / リアルタイム]
  ⑥ ランタイムSDK   JSON読込 + 音源同期 + データ供給API   ← 契約B（SDK↔描画）
  ⑦ 描画           開発者が p5.js / three.js 等で実装

[出力]  インタラクティブWebアプリ  /  MP4書き出し
```

- **契約A（JSON）**：オフラインとオンラインの握手。解析器を差し替えても JSON 形式さえ守れば SDK 側は無傷。
- **契約B（API）**：SDK と描画の握手。SDK は時間とデータだけ、レンダラはピクセルだけを担当。これにより同一 SDK で p5.js でも three.js でも動く。

---

## 4. 技術選定

### 4.1 音源分離 ― Demucs より良い選択肢

音源分離は音響処理であり**言語非依存**（日本語でも問題なく動く）。ただしボーカル品質は後段の歌詞アライメント精度を左右するため、選定は重要。

| モデル | 位置づけ | 用途の推奨 |
|---|---|---|
| **htdemucs_ft** | 無難な定番。素の htdemucs より明確に良い。速い・依存軽い | 構造解析用・声量用には十分 |
| **Mel-Band / BS-RoFormer** | ボーカル分離の現 SOTA（mel スケール投影で中高域＝声に強い） | **歌詞アライメント用のボーカルはこれを推奨** |
| Spleeter | 2019年・軽量だが品質差が可聴 | 非推奨（プロトタイプ用途のみ） |

**方針：二刀流。** 構造解析は allin1 内蔵の Demucs に任せ、**歌詞アライメント用のボーカルだけ RoFormer で別途抽出**する。一番つらい工程（アライメント）にだけ最良の分離器を投入するのが費用対効果が高い。

> 注意：「Demucs」には音楽分離の `demucs`（htdemucs）と、音声ノイズ除去用の `denoiser` の2系統がある。本プロジェクトで使うのは前者（音楽音源分離・MUSDB18 学習）。speech enhancement 系とは別物。

### 4.2 工程別ツール一覧

| 工程 | 採用（既定） | 代替・上位 | 備考 |
|---|---|---|---|
| 音源取得 | yt-dlp + ffmpeg | — | mp3 化せず可逆 WAV マスターを作る |
| 音源分離 | htdemucs_ft | Mel-Band/BS-RoFormer | 上記 4.1 の二刀流 |
| 楽曲地図 | allin1 | madmom, BeatNet | tempo/beat/downbeat/構造を一括取得 |
| コード進行 | autochord | madmom, BTC（深層） | |
| 声量 | librosa RMS（分離ボーカル） | Web Audio AnalyserNode（ライブ時） | |
| ムード(V/A) | Essentia 事前学習モデル | tempo+調性+energy の簡易プロキシ | 誤差許容領域 |
| 歌詞取得 | syncedlyrics / LRCLIB | LDDC（逐字）, NetEase/QQ | ボカロは NetEase を上位に |
| 歌詞アライメント | MFA（pyopenjtalk g2p） | WhisperX（日本語 wav2vec2）, モーラ按分 | 行窓内アライメント |
| 形態素解析 | fugashi/MeCab or Sudachi | — | word 分割＋モーラ取得 |
| 文字起こし(ASR) | Whisper（歌詞ゼロ時のみ） | Kotoba-Whisper, ReazonSpeech | フォールバック用途に限定 |

### 4.3 音源取得の注意

- YouTube の素音声は Opus/AAC。`--audio-format mp3` は二重劣化になるため避け、`bestaudio` を取得して **可逆 WAV マスター（44.1kHz/ステレオ）** に変換する。
- mp3 はエンコーダ遅延（先頭パディング）でフレーム同期がずれる事故の元。タイト同期を狙う本用途では不適。
- 16kHz への変換等は各ツールが内部で行うため、こちらは高品質マスター1本を保持し各段に渡す。

---

## 5. 歌詞取得のフォールバック設計

当たり判定の高い順に降りていく。降りるほど後段の負荷が増す。

| Tier | ソース | 得られるもの | 後段の負荷 |
|---|---|---|---|
| T1 | syncedlyrics `enhanced=True` / LDDC 逐字 | **word/char レベル**同期 | ほぼゼロ（char 分割のみ） |
| T2 | LRCLIB / NetEase（行単位 LRC） | テキスト＋**行レベル**時刻 | 軽（行窓内で word/char 細分化） |
| T3 | Genius 等（プレーン） | テキストのみ | 重（フルアライメント） |
| T4 | なし | — | 最重（ASR 転写 → T3 扱い） |

- **ボカロでは NetEase/QQ を LRCLIB より上位に置く**（中国のボカロシーンが大きく、同期歌詞・逐字のカバレッジが高いことが多い）。
- LRCLIB はクラウドソースで西洋ポップ寄り＋メタデータマッチが弱いため、ボカロでは穴がある前提でフォールバックを厚くする。

---

## 6. 歌詞アライメントの方針

- **基本は「転写」ではなく「整合」**：既知歌詞を時間軸に貼る forced alignment。歌詞が取れていれば ASR は不要。
- **行レベル時刻があれば制約付き問題に縮小**：各行の時間窓の中だけでアライメントすればよく、暴れが抑えられる。
- **日本語の文字レベル**：行/単語を fugashi・Sudachi で形態素分割し、pyopenjtalk で読み（モーラ）を取得。char タイミングは「窓内をモーラ数で按分」または「分離ボーカルのオンセットへスナップ」。
- 産総研（TextAlive 本家）の手法も「歌詞→音素列→音素ネットワーク→Viterbi」であり、本方針（g2p + forced aligner）と同系。

---

## 7. データスキーマ（契約A / TextAlive 互換 JSON）

```jsonc
{
  "song": {
    "title": "...", "artist": "...",
    "durationMs": 0,
    "source": { "type": "youtube", "id": "...", "offsetMs": 0 }
  },
  "phrases": [
    {
      "startTime": 0, "endTime": 0, "text": "...",
      "words": [
        {
          "startTime": 0, "endTime": 0, "text": "...", "pos": "名詞",
          "chars": [ { "startTime": 0, "endTime": 0, "char": "あ" } ]
        }
      ]
    }
  ],
  "beats":    [ { "startTime": 0, "position": 1 } ],
  "chords":   [ { "startTime": 0, "endTime": 0, "name": "Cmaj7" } ],
  "segments": [ { "startTime": 0, "endTime": 0, "label": "chorus" } ],
  "amplitude":     [ { "time": 0, "value": 0.0 } ],
  "valenceArousal":[ { "time": 0, "valence": 0.0, "arousal": 0.0 } ]
}
```

- `source.offsetMs`：ストリーム再生音源と解析音源の頭ズレを吸収するキャリブレーション値。
- 各時刻は ms 単位。`position` は小節内の拍番号。

---

## 8. ランタイム SDK の API（契約B）

TextAlive App API のインターフェースを踏襲し、移植性を確保する。

```typescript
interface Player {
  load(json: LyricData, audio: AudioSource): Promise<void>;

  // ライフサイクル / 再生制御
  on(event: "ready" | "timeupdate" | "play" | "pause" | "end", cb: Function): void;
  play(): void; pause(): void; seek(ms: number): void;

  // 現在時刻のデータ参照
  readonly position: number;            // 現在の再生位置 [ms]
  findBeat(ms: number): Beat | null;
  findChorus(ms: number): Segment | null;
  findChord(ms: number): Chord | null;

  // 発声中の歌詞（phrase / word / char）
  currentPhrase(ms: number): Phrase | null;
  currentWord(ms: number): Word | null;
  currentChar(ms: number): Char | null;

  // 表現用パラメータ
  getVocalAmplitude(ms: number): number;
  getValenceArousal(ms: number): { valence: number; arousal: number };
}
```

- 描画は一切しない。開発者は `timeupdate` や各 `find*` / `current*` を購読し、自前のレンダラで「いつ・何が・どのくらいの強さで」を絵にする。
- **手動上書きフック**：自動生成値を叩き台として、word/char の時刻や segment 境界を手で nudge できる API を用意する（後述のボカロ対応で必須）。

---

## 9. 日本語・ボカロ対応の要件

全段で domain shift（学習分布からのズレ）を受けるため、**音声モデルへの依存を最小化する**設計とする。

### 段階別の難易度
- **音源分離**：言語非依存で基本平気。ただしボカロの爆音・高密度ミックス（強コンプ、レイヤー/チョップ、過剰オートチューン）は荒れやすく、品質は曲依存。
- **ASR（転写）**：日本語で弱く、歌唱＋合成音声で二重に弱い。→ 転写は「歌詞ゼロ時のみ」に限定。
- **フォースアライメント**：日本語は torchaudio デフォルト align モデルが無く、HF の日本語 wav2vec2 か MFR が必要。精度重視なら MFA（pyopenjtalk g2p）。

### ボカロ戦略（2本柱）
1. **逐字/word-level 歌詞の取得に全振り**：NetEase/QQ のカバレッジを活かす。取得側が強いほど音声モデルに頼らずに済む。
2. **モーラ按分を安全網に**：行レベル時刻さえあれば、pyopenjtalk のモーラ数で按分するだけで char 時刻が出る（**音声非依存・GPU不要・ほぼ一瞬**）。オンセットスナップは分離が綺麗な時のみの補正に格下げ。

### 既知の地雷：メリスマ / ロングトーン
1モーラを複数音符に引き伸ばす歌唱がボカロで多発し、素朴な按分もアライナーも崩す。分離ボーカルの声量エンベロープで検出・補正できるが、完全自動は困難。**「良い自動ドラフト＋手で直せるフック」を現実的ゴールとする**（TextAlive 本家も半自動）。

---

## 10. 処理コストの考え方

歌詞パイプラインと楽曲地図パイプラインで負荷が分かれる。

| ケース | 歌詞の word/char 化 | 楽曲地図（allin1 等） | 総合 |
|---|---|---|---|
| 有名曲 + 同期歌詞 + モーラ按分 | 激軽（音声不要・GPU不要） | GPU 1回 | 軽 |
| プレーン歌詞のみ | 重（分離＋アライメント） | GPU 1回 | 中〜重 |
| 歌詞なし（無名ボカロ等） | 最重（ASR＋アライメント） | GPU 1回 | 重 |

- **二極化**：有名曲は激軽・無名曲は重い、というはっきりした傾向になる。
- 「歌詞が軽い ≠ 全体が軽い」。サビ/キメ連動のため楽曲地図側の GPU コストは曲ごとに常に残る。
- EVO-X2（Ryzen AI MAX+ 395 / 96GB）ならオフライン一括解析はローカルで現実的。

---

## 11. 出力形態

| 形態 | 構成 | 長所 | 短所 |
|---|---|---|---|
| インタラクティブ Web アプリ | SDK + p5.js/three.js、音は YouTube IFrame 同期 | 著作権が最もクリーン、TextAlive 演出資産を流用可 | MP4 にならない、同期に微ジッタ（要オフセット校正） |
| MP4 書き出し | 同 SDK + CCapture/ffmpeg or Remotion | フレーム完全同期・再現性 | 音源実ファイルが必要、レンダ時間、Remotion は商用ライセンス条件あり |

両者は**同一の JSON / SDK を共有**し、レンダラ層だけが異なる。

---

## 12. 段階的開発方針

1. **Phase 1 ― 共通バックボーン**：取得→分離→楽曲地図→歌詞整合→JSON を一通り通し、**JSON スキーマを確定**する。まずは有名ボカロ曲（ミドルテンポ・ボーカルがクリアな曲）で素の精度を測る。
2. **Phase 2 ― ランタイム SDK + p5.js リファレンス**：契約B の API を実装し、p5.js で最小の文字PV を描画。同一 JSON で MP4 書き出し経路も確認。
3. **Phase 3 ― 日本語・ボカロ堅牢化**：フォールバック階層、モーラ按分、メリスマ補正、手動上書きフックを整備。
4. **Phase 4 ― 任意**：LLM によるアートディレクション（抽出特徴量をテキストで渡し、セクション別の演出指示を生成）。Shi のローカル LLM 資産（テキストモデルで可）を活用。

---

## 13. リスク・未解決事項

- **最大ボトルネック：日本語×ボカロの word/char アライメント精度**。早期に実曲で当たり判定を取る必要がある。
- メリスマ/ロングトーンの自動処理は限界があり、手動補正前提。
- LRCLIB のボカロカバレッジ不足。NetEase 依存度が上がると、その可用性がリスクに。
- 音源分離の品質が曲依存（攻めた音圧系で劣化）。
- MP4 出力で Remotion を使う場合のライセンス条件。
- 同期歌詞のフォーマット差（LRC / 逐字LRC / YRC）を内部スキーマへ正規化する処理の整備。

---

## 14. 参考（主要 OSS / ツール）

- 取得：yt-dlp, ffmpeg
- 分離：Demucs(htdemucs_ft), Mel-Band/BS-RoFormer（UVR / audio-separator 経由）
- 楽曲地図：allin1, madmom, BeatNet, autochord, librosa, Essentia
- 歌詞取得：syncedlyrics, LRCLIB, LDDC, NetEase/QQ
- アライメント：MFA, WhisperX, torchaudio forced_align, pyopenjtalk
- 形態素：fugashi/MeCab, Sudachi
- 描画：p5.js, three.js（開発者選択）
- 書き出し：CCapture.js, ffmpeg, Remotion
- 参考設計：TextAlive App API（インターフェース）, nomadkaraoke/karaoke-generator（自動パイプライン）
