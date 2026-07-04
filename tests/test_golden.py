"""契約A (schemaVersion 導入後) の golden file テスト。

tests/golden/ 配下の各 Tier フィクスチャは to_dict() の実出力を凍結したもの。
load → to_dict の再シリアライズがファイル内容と完全一致することを確認し、
将来のリファクタで契約Aのキー順・丸め・フィールド構成が意図せず変わることを
機械的に防ぐ (契約凍結)。
"""

import json
from pathlib import Path

import pytest

from lyricpv.schema import LyricData, SchemaError, from_dict

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_FILES = ["t1_word_synced.json", "t2_line_synced.json", "t3_plain.json"]


def _dump(data: LyricData) -> str:
    # LyricData.save() と同じシリアライズ条件 (契約凍結の比較対象を合わせる)。
    return json.dumps(data.to_dict(), ensure_ascii=False, indent=1, allow_nan=False)


@pytest.mark.parametrize("filename", GOLDEN_FILES)
def test_golden_loads_successfully(filename):
    path = GOLDEN_DIR / filename
    data = LyricData.load(path)
    assert data.song.title
    assert data.phrases


@pytest.mark.parametrize("filename", GOLDEN_FILES)
def test_golden_round_trip_is_byte_identical(filename):
    path = GOLDEN_DIR / filename
    raw = path.read_text(encoding="utf-8")
    data = LyricData.load(path)
    assert _dump(data) == raw


def test_from_dict_accepts_legacy_dict_without_schema_version():
    path = GOLDEN_DIR / "t1_word_synced.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    del legacy["schemaVersion"]
    data = from_dict(legacy)  # 例外が出なければ OK (欠落は後方互換で許容)
    assert data.song.title == "月とツクヨミ"


def test_from_dict_rejects_major_version_mismatch():
    path = GOLDEN_DIR / "t1_word_synced.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schemaVersion"] = "2.0"
    with pytest.raises(SchemaError):
        from_dict(data)


def test_from_dict_accepts_minor_version_difference():
    path = GOLDEN_DIR / "t1_word_synced.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schemaVersion"] = "1.99"
    from_dict(data)  # メジャー一致なら minor 違いは許容 (例外が出なければ OK)
