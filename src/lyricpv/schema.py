"""契約A: TextAlive 互換 JSON のスキーマ定義と入出力。

オフライン解析器とランタイム SDK の握手となるデータ形式。
解析器を差し替えてもこの形式さえ守れば SDK 側は無傷、という
不変条件を維持するため、検証ロジックもここに集約する。

時刻はすべてミリ秒 (ms) の整数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """契約A の JSON が不正なときに送出される。"""


@dataclass
class Char:
    start_time: int
    end_time: int
    char: str

    def to_dict(self) -> dict[str, Any]:
        return {"startTime": self.start_time, "endTime": self.end_time, "char": self.char}


@dataclass
class Word:
    start_time: int
    end_time: int
    text: str
    pos: str = ""
    chars: list[Char] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "startTime": self.start_time,
            "endTime": self.end_time,
            "text": self.text,
            "pos": self.pos,
            "chars": [c.to_dict() for c in self.chars],
        }


@dataclass
class Phrase:
    start_time: int
    end_time: int
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "startTime": self.start_time,
            "endTime": self.end_time,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }


@dataclass
class Beat:
    start_time: int
    position: int  # 小節内の拍番号 (1 始まり)

    def to_dict(self) -> dict[str, Any]:
        return {"startTime": self.start_time, "position": self.position}


@dataclass
class Chord:
    start_time: int
    end_time: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"startTime": self.start_time, "endTime": self.end_time, "name": self.name}


@dataclass
class Segment:
    start_time: int
    end_time: int
    label: str  # "chorus" / "verse" / "intro" など

    def to_dict(self) -> dict[str, Any]:
        return {"startTime": self.start_time, "endTime": self.end_time, "label": self.label}


@dataclass
class AmplitudePoint:
    time: int
    value: float  # 0.0–1.0 に正規化した声量

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time, "value": self.value}


@dataclass
class VAPoint:
    time: int
    valence: float  # -1.0–1.0
    arousal: float  # -1.0–1.0

    def to_dict(self) -> dict[str, Any]:
        return {"time": self.time, "valence": self.valence, "arousal": self.arousal}


@dataclass
class SongSource:
    type: str  # "youtube" | "file"
    id: str  # YouTube 動画 ID またはファイル名
    offset_ms: int = 0  # ストリーム再生音源と解析音源の頭ズレ補正値

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "offsetMs": self.offset_ms}


@dataclass
class SongMeta:
    title: str
    artist: str
    duration_ms: int
    source: SongSource

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "durationMs": self.duration_ms,
            "source": self.source.to_dict(),
        }


@dataclass
class LyricData:
    """契約A のルートオブジェクト。"""

    song: SongMeta
    phrases: list[Phrase] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    amplitude: list[AmplitudePoint] = field(default_factory=list)
    valence_arousal: list[VAPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "song": self.song.to_dict(),
            "phrases": [p.to_dict() for p in self.phrases],
            "beats": [b.to_dict() for b in self.beats],
            "chords": [c.to_dict() for c in self.chords],
            "segments": [s.to_dict() for s in self.segments],
            "amplitude": [a.to_dict() for a in self.amplitude],
            "valenceArousal": [v.to_dict() for v in self.valence_arousal],
        }

    def save(self, path: str | Path) -> None:
        data = self.to_dict()
        validate(data)
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "LyricData":
        return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def from_dict(data: dict[str, Any]) -> LyricData:
    """検証済み dict から LyricData を復元する。"""
    validate(data)
    song = data["song"]
    src = song["source"]
    return LyricData(
        song=SongMeta(
            title=song["title"],
            artist=song["artist"],
            duration_ms=song["durationMs"],
            source=SongSource(type=src["type"], id=src["id"], offset_ms=src.get("offsetMs", 0)),
        ),
        phrases=[
            Phrase(
                start_time=p["startTime"],
                end_time=p["endTime"],
                text=p["text"],
                words=[
                    Word(
                        start_time=w["startTime"],
                        end_time=w["endTime"],
                        text=w["text"],
                        pos=w.get("pos", ""),
                        chars=[
                            Char(start_time=c["startTime"], end_time=c["endTime"], char=c["char"])
                            for c in w.get("chars", [])
                        ],
                    )
                    for w in p.get("words", [])
                ],
            )
            for p in data.get("phrases", [])
        ],
        beats=[Beat(start_time=b["startTime"], position=b["position"]) for b in data.get("beats", [])],
        chords=[
            Chord(start_time=c["startTime"], end_time=c["endTime"], name=c["name"])
            for c in data.get("chords", [])
        ],
        segments=[
            Segment(start_time=s["startTime"], end_time=s["endTime"], label=s["label"])
            for s in data.get("segments", [])
        ],
        amplitude=[AmplitudePoint(time=a["time"], value=a["value"]) for a in data.get("amplitude", [])],
        valence_arousal=[
            VAPoint(time=v["time"], valence=v["valence"], arousal=v["arousal"])
            for v in data.get("valenceArousal", [])
        ],
    )


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SchemaError(message)


def validate(data: dict[str, Any]) -> None:
    """契約A の JSON (dict) を検証し、不正なら SchemaError を送出する。

    検証内容: 必須キー、時刻の単調性 (start <= end)、リスト要素の時刻昇順。
    """
    _require(isinstance(data, dict), "ルートはオブジェクトである必要があります")
    song = data.get("song")
    _require(isinstance(song, dict), "song がありません")
    for key in ("title", "artist", "durationMs", "source"):
        _require(key in song, f"song.{key} がありません")
    _require(song["durationMs"] > 0, "song.durationMs は正の値である必要があります")
    src = song["source"]
    for key in ("type", "id"):
        _require(key in src, f"song.source.{key} がありません")

    prev_phrase_start = -1
    for p in data.get("phrases", []):
        _require(p["startTime"] <= p["endTime"], f"phrase の時刻が逆転: {p.get('text', '')!r}")
        _require(p["startTime"] >= prev_phrase_start, "phrases は startTime 昇順である必要があります")
        prev_phrase_start = p["startTime"]
        for w in p.get("words", []):
            _require(w["startTime"] <= w["endTime"], f"word の時刻が逆転: {w.get('text', '')!r}")
            for c in w.get("chars", []):
                _require(c["startTime"] <= c["endTime"], f"char の時刻が逆転: {c.get('char', '')!r}")

    for name, items in (("beats", "startTime"), ("amplitude", "time"), ("valenceArousal", "time")):
        times = [item[items] for item in data.get(name, [])]
        _require(times == sorted(times), f"{name} は時刻昇順である必要があります")

    for name in ("chords", "segments"):
        for item in data.get(name, []):
            _require(
                item["startTime"] <= item["endTime"],
                f"{name} の時刻が逆転: {item.get('name', item.get('label', ''))!r}",
            )
