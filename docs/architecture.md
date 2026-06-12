# アーキテクチャと設計判断

要件定義([lyric-pv-sdk-requirements.md](lyric-pv-sdk-requirements.md))をベースに、macOS (Apple Silicon / MPS) で確実に動くことを優先して構成した MVP の設計記録。

## 全体像

```
src/lyricpv/
├── schema.py       契約A: TextAlive 互換 JSON の定義・検証・入出力
├── device.py       MPS / CUDA / CPU の自動選択(MPS フォールバック有効化込み)
├── fetch.py        ① 取得   yt-dlp → 44.1kHz 可逆 WAV マスター
├── separate.py     ② 分離   Demucs htdemucs(MPS、失敗時 CPU リトライ)
├── music_map.py    ③ 楽曲地図 librosa(ビート/構造/コード/声量/V-A)
├── lyrics/         ④ 歌詞   取得 → LRC パース → 形態素 → モーラ按分
├── pipeline.py     ⑤ 統合   ①→④ を契約A JSON へ規格化
└── cli.py          lyricpv analyze コマンド

sdk/lyric-player.mjs   契約B: ランタイム SDK(依存ゼロ ES module)
```

2つの契約はそのまま要件定義 3 章に対応する:

- **契約A**(`schema.py` ⇔ `lyric_data.json`): 解析器を差し替えても JSON 形式さえ守れば SDK 側は無傷
- **契約B**(`Player` API): SDK は時間とデータだけ、レンダラはピクセルだけを担当

## 要件定義からの調整点(ADR)

### 1. 楽曲地図: allin1 → librosa ベース

**判断**: allin1 は natten(ネイティブビルド)依存で macOS への導入が不安定なため、MVP では librosa で代替する。

- ビート: librosa の動的計画法ビートトラッカー
- ダウンビート: 4拍子を仮定し、拍ごとのオンセット強度が最大になる位相を選ぶ簡易推定
- 構造: chroma+MFCC の凝集クラスタリング + 「反復 × エネルギー」のサビ推定ヒューリスティック
- コード: クロマのテンプレートマッチ(maj/min 24種)

**帰結**: 精度は allin1 に劣る(特にダウンビート・構造ラベル)。契約A の形式は変わらないため、Linux/GPU 環境が用意できた時点で allin1 実装に差し替え可能。

### 2. 音源分離: 二刀流 → htdemucs 単独(MPS)

**判断**: 要件定義の「歌詞アライメント用だけ RoFormer」は、MVP の主アライメント経路がモーラ按分(音声非依存)であるため不要。分離ボーカルの用途は声量エンベロープと歌唱区間推定に限られ、htdemucs で十分。

- MPS で実行し、未対応演算は `PYTORCH_ENABLE_MPS_FALLBACK=1` で CPU に逃がす(`device.py` が torch import 前に設定)
- 丸ごと失敗した場合は CPU で 1 回リトライ
- `--model htdemucs_ft` で品質優先(約4倍遅)に切り替え可能

#### 2a. 改訂(#3): opt-in のボーカル強化 2 段パイプラインを追加

**判断の変更**: 「RoFormer 不要」は *既定経路* については維持するが、サビのハモリ・エコーが声量ベースの歌唱区間推定を膨らませる問題(#3)への対症として、Demucs の vocals に対する**任意の後段処理**を `enhance.py` に追加した。Demucs の置き換えではない。

- `--enhance-vocals`(extra: `enhance` = audio-separator)で有効化。既定 OFF(モデル DL と推論が重い)
- 1 段目: Mel-Band RoFormer カラオケ系モデルでリードボーカル抽出(ハモリ除去)
- 2 段目: DeEcho/DeReverb 系モデルで残響除去
- 使用モデルは `meta.json` の `enhanceModels` に記録(再現性のため)
- モデルの優劣(特に日本語ポップス)は流動的なので、既定モデルは `audio-separator --list_models` を見て差し替えてよい

あわせて声量エンベロープを用途別に分離した:

- `amplitude`(契約A): 生の RMS。SDK の `getVocalAmplitude()`(演出用)の意味を保つためゲートを掛けない
- `vocal_activity`(契約A 外・内部用): オンセット減衰ゲート済み。エコー/ハモリの余韻は新たな立ち上がりを持たない性質を使って尾を抑え、`align()` の歌唱区間推定にだけ使う

T3(プレーン歌詞)の按分も「全体スパン両端のみ」から「有声区間リストへの配分(間奏スキップ)」に変更し、エンベロープが実際に行配置へ効くようにした。

### 3. 読み取得: pyopenjtalk → fugashi + unidic-lite

**判断**: pyopenjtalk はビルド依存(cmake 等)があり導入が重い。UniDic の仮名読みフィールドからモーラを数える方式なら純 Python 依存で済み、モーラ按分には十分な精度。

### 4. アライメント: MFA → モーラ按分を主経路に

**判断**: 要件定義 9 章の「安全網」であるモーラ按分を MVP の主経路とする。行レベル時刻(T2)さえあれば音声モデル非依存・GPU 不要で char 時刻が出るため、日本語×ボカロの domain shift の影響を受けない。

- T1(逐字 LRC): word 時刻は実測値、char のみ按分
- T2(行 LRC): 行窓内を単語のモーラ数比で按分。行間の長い間奏は「モーラ数 × 500ms + 800ms」で切り上げ
- T3(プレーン): 声量エンベロープから歌唱区間を推定し、行をモーラ数比で配置(粗い叩き台)
- char 按分の重み: 小書き仮名 0.3 / 促音・撥音・長音 0.6 / 通常 1.0、漢字は均等割り

**帰結**: メリスマ/ロングトーンで誤差が出る(要件定義の既知の地雷)。「良い自動ドラフト + 手で直せるフック」の方針通り、SDK の `applyOverrides()` で補正する。MFA / WhisperX による精密アライメントは将来の差し替え候補(契約A は不変)。

#### 4a. 改訂(#3, #6): opt-in の強制アラインメント補正を追加

**判断の変更**: モーラ按分は主経路(既定)として維持するが、T2 では行内時刻が完全に推定値で、サビ(メリスマ・ロングトーン・ハモリ)の体感ズレの主因になる。分離品質の改善(2a)は声量エンベロープを使う T3 経路にしか効かない — `meta.json` の `lyricsTier` が T2 の曲では分離をいくら強化しても同期は変わらない — ため、行内時刻そのものを実測に置き換える補正を `refine.py` に追加した。

- `--refine-align`(extra: `refine` = whisperx)で有効化。既定 OFF(モデル DL と推論が重い)
- whisperx の日本語 CTC アラインメント(wav2vec2、既定 `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` を明示指定)を分離ボーカル(強化済みがあればそれ)に適用
- 行窓は既存値(LRC または按分)を ±400ms 広げて探索範囲とし、行の大きな取り違えを防ぐ。**LRC の行時刻自体がこれ以上ずれている場合は補正しきれない**(その場合は LRC ソースの差し替えが先)
- 文字マッチ率 50% 未満の行は按分値のまま残す(ラララ等の歌詞と歌唱の不一致、間奏の誤検出に対する安全網)。実測が付かない文字は前後の確定点から補間
- 使用モデルと補正行数は `meta.json` の `refineModel` / `refinedPhrases` に記録

これは #6(読み取得失敗時の等間隔按分)の実効的な解にもなる: 按分がどれだけ粗くても、最終時刻は実測で上書きされる。

### 5. ムード(V/A): Essentia → 簡易プロキシ

**判断**: 要件定義 4.2 の代替案「tempo+調性+energy の簡易プロキシ」を採用(誤差許容領域)。

- valence: Krumhansl プロファイルとの長短調相関差(70%)+ スペクトル重心の明るさ(30%)
- arousal: テンポ(50%)+ RMS エネルギー(50%)、5 秒窓で時系列化

### 6. WebUI: 解析フロントエンドに限定

WebUI は「解析の投入・進捗・JSON 取得」だけを担う薄い任意機能(`--extra webui`)とし、描画・アニメーションは持たせない(SDK 利用者の責務 — 設計思想の通り)。

- `webui/jobs.py`: ワーカースレッドによるインメモリのジョブ管理(ローカルツール想定で永続化なし)
- `webui/app.py`: FastAPI。投入 `POST /api/songs` → 進捗 `GET /api/jobs/{id}` → 取得 `GET /api/songs/{id}/lyric_data.json`
- フロントは vanilla JS の 1 ページのみ
- 実機確認済み: 実 HTTP 経由のフル解析で `デバイス: mps` を確認(Demucs 分離が MPS で実行される)

## 処理フロー(pipeline.py)

```
source(URL/ファイル)
  → fetch    : bestaudio → 44.1kHz/16bit WAV マスター(mp3 を経由しない: 二重劣化・先頭パディング回避)
  → separate : vocals.wav / accompaniment.wav(skip_separation で省略可)
  → music_map: beats / segments / chords / amplitude / valenceArousal
  → lyrics   : ユーザー供給テキスト > syncedlyrics(T1→T2)> なし(T4)
  → align    : phrase→word→char へモーラ按分
  → save     : lyric_data.json(保存前に schema.validate で検証)+ meta.json
```

進捗は `progress(stage, message)` コールバックで通知され、CLI が stderr に表示する。

## 既知の制約・今後の課題

- ダウンビート・構造ラベルは簡易推定(allin1 差し替えで改善余地)
- メリスマ/ロングトーンの自動補正なし(手動 `applyOverrides` 前提)
- コードはトライアド 24 種のみ(7th 等は最近傍のトライアドに丸まる)
- 歌詞検索のカバレッジは syncedlyrics のプロバイダ依存(ボカロは `--vocaloid` で NetEase 優先)
- YouTube IFrame 同期アダプタ・MP4 書き出しは未実装(`manualClockAdapter` がフレーム逐次レンダリングの土台)
- 強制アラインメント(4a)は opt-in。既定経路の T1/T2 行内按分は依然モーラ比の推定(#3・#6 の根本解決は `--refine-align` を使う)
- `--refine-align` は LRC の行時刻を ±400ms までしか補正しない。行時刻自体が大きくずれた LRC はソース差し替えか手動修正が必要
