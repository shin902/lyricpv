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

### 3. 読み取得: pyopenjtalk → fugashi + unidic-lite

**判断**: pyopenjtalk はビルド依存(cmake 等)があり導入が重い。UniDic の仮名読みフィールドからモーラを数える方式なら純 Python 依存で済み、モーラ按分には十分な精度。

### 4. アライメント: MFA → モーラ按分を主経路に

**判断**: 要件定義 9 章の「安全網」であるモーラ按分を MVP の主経路とする。行レベル時刻(T2)さえあれば音声モデル非依存・GPU 不要で char 時刻が出るため、日本語×ボカロの domain shift の影響を受けない。

- T1(逐字 LRC): word 時刻は実測値、char のみ按分
- T2(行 LRC): 行窓内を単語のモーラ数比で按分。行間の長い間奏は「モーラ数 × 500ms + 800ms」で切り上げ
- T3(プレーン): 声量エンベロープから歌唱区間を推定し、行をモーラ数比で配置(粗い叩き台)
- char 按分の重み: 小書き仮名 0.3 / 促音・撥音・長音 0.6 / 通常 1.0、漢字は均等割り

**帰結**: メリスマ/ロングトーンで誤差が出る(要件定義の既知の地雷)。「良い自動ドラフト + 手で直せるフック」の方針通り、SDK の `applyOverrides()` で補正する。MFA / WhisperX による精密アライメントは将来の差し替え候補(契約A は不変)。

### 5. ムード(V/A): Essentia → 簡易プロキシ

**判断**: 要件定義 4.2 の代替案「tempo+調性+energy の簡易プロキシ」を採用(誤差許容領域)。

- valence: Krumhansl プロファイルとの長短調相関差(70%)+ スペクトル重心の明るさ(30%)
- arousal: テンポ(50%)+ RMS エネルギー(50%)、5 秒窓で時系列化

### 6. WebUI: スコープ外

当初計画にあった WebUI(解析フロントエンド)はユーザー判断でスコープから削除。解析は CLI(`lyricpv analyze`)、描画アニメーションは本プロジェクトの管轄外(SDK 利用者の責務)。

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
