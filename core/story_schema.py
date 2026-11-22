"""Hợp đồng dữ liệu và kiểm tra tính hợp lệ của kịch bản phân cảnh Storyboard (Phase 3, v2.0 & v3.0).

Hỗ trợ cả Storyboard v1, v2.0 Hybrid (flow_video + static_image) và v3.0 Continuous Narrative (beats + locations).
Tuân thủ nghiêm ngặt nguyên tắc không import GUI / tkinter trong core/.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .errors import DubberError
from .frame_presets import DEFAULT_FRAME_PRESET, normalize_frame_preset
from .sfx_engine import SFX_VALID_TAGS

VALID_MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right", "static"}
VALID_SFX = set(SFX_VALID_TAGS)

DEFAULT_MOTION = "zoom_in"
DEFAULT_SFX = "none"


@dataclass
class StoryScene:
    id: int
    speech: str
    source_note: str = ""
    asset_type: str = "static_image"       # "flow_video" hoặc "static_image"
    image_prompt: str = ""
    motion_prompt: str = ""
    motion: str = DEFAULT_MOTION
    ken_burns: str = ""                    # Đồng nghĩa với motion trong v2.0
    sfx: str = DEFAULT_SFX
    on_screen_text: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_sec: float = 0.0
    words: list = field(default_factory=list)

    def __post_init__(self):
        try:
            self.id = int(self.id)
        except (ValueError, TypeError) as e:
            raise DubberError(f"Cảnh có id không hợp lệ: {self.id}") from e

        self.speech = str(self.speech).strip()
        if not self.speech:
            raise DubberError(f"Cảnh id={self.id} có 'speech' rỗng")

        self.source_note = str(self.source_note).strip()
        self.image_prompt = str(self.image_prompt).strip()
        self.motion_prompt = str(self.motion_prompt).strip()
        self.on_screen_text = str(self.on_screen_text).strip()

        # Chuẩn hóa asset_type
        raw_asset = str(self.asset_type).strip().lower()
        if raw_asset in ("flow_video", "video", "clip"):
            self.asset_type = "flow_video"
        else:
            self.asset_type = "static_image"

        # Kiểm tra motion (báo lỗi rõ ràng, không fallback im lặng)
        effective_motion = self.ken_burns or self.motion or DEFAULT_MOTION
        norm_motion = str(effective_motion).strip().lower()
        if norm_motion not in VALID_MOTIONS:
            raise DubberError(f"Cảnh id={self.id} có motion '{effective_motion}' không hợp lệ (chấp nhận: {sorted(VALID_MOTIONS)})")
        self.motion = norm_motion
        self.ken_burns = norm_motion

        # Kiểm tra sfx (báo lỗi rõ ràng, không fallback im lặng)
        norm_sfx = str(self.sfx).strip().lower() if self.sfx else DEFAULT_SFX
        clean_sfx = re.sub(r"[^\w\-]", "", norm_sfx) if norm_sfx else DEFAULT_SFX
        if clean_sfx not in VALID_SFX:
            raise DubberError(f"Cảnh id={self.id} có sfx '{self.sfx}' không hợp lệ (chấp nhận: {sorted(VALID_SFX)})")
        self.sfx = clean_sfx

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "speech": self.speech,
            "source_note": self.source_note,
            "asset_type": self.asset_type,
            "image_prompt": self.image_prompt,
            "motion_prompt": self.motion_prompt,
            "motion": self.motion,
            "ken_burns": self.ken_burns,
            "sfx": self.sfx,
            "on_screen_text": self.on_screen_text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": self.duration_sec,
            "words": self.words,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StoryScene:
        if not isinstance(data, dict):
            raise DubberError("Dữ liệu cảnh phải là JSON object (dict)")
        if "id" not in data:
            raise DubberError("Cảnh thiếu trường bắt buộc 'id'")
        if "speech" not in data:
            raise DubberError(f"Cảnh id={data.get('id', '?')} thiếu trường bắt buộc 'speech'")

        ken_burns_val = data.get("ken_burns", "")
        motion_val = data.get("motion", ken_burns_val or DEFAULT_MOTION)

        return cls(
            id=data["id"],
            speech=data["speech"],
            source_note=data.get("source_note", ""),
            asset_type=data.get("asset_type", "static_image"),
            image_prompt=data.get("image_prompt", ""),
            motion_prompt=data.get("motion_prompt", ""),
            motion=motion_val,
            ken_burns=ken_burns_val or motion_val,
            sfx=data.get("sfx", DEFAULT_SFX),
            on_screen_text=data.get("on_screen_text", ""),
            start_time=float(data.get("start_time", 0.0)),
            end_time=float(data.get("end_time", 0.0)),
            duration_sec=float(data.get("duration_sec", 0.0)),
            words=data.get("words", []),
        )


@dataclass
class Storyboard:
    title: str
    version: str = "1.0"
    aspect_ratio: str = "9:16"
    frame_preset: str = DEFAULT_FRAME_PRESET
    total_words: int = 0
    style_prefix: str = ""
    character_bible: str = ""
    full_script_for_elevenlabs: str = ""
    style_suffix: str = ""
    scenes: list[StoryScene] = field(default_factory=list)
    beats: list[dict] = field(default_factory=list)
    locations: dict[str, Any] = field(default_factory=dict)
    bgm: str = ""

    def __post_init__(self):
        self.title = str(self.title).strip()
        self.version = str(self.version).strip() or "1.0"
        self.aspect_ratio = str(self.aspect_ratio).strip() or "9:16"
        self.frame_preset = normalize_frame_preset(self.frame_preset, allow_auto=False)
        self.style_prefix = str(self.style_prefix).strip()
        self.character_bible = str(self.character_bible).strip()
        self.full_script_for_elevenlabs = str(self.full_script_for_elevenlabs).strip()
        self.style_suffix = str(self.style_suffix).strip()
        self.bgm = str(self.bgm).strip()

        if (not self.scenes or not isinstance(self.scenes, list)) and (not self.beats or not isinstance(self.beats, list)):
            raise DubberError("Storyboard thiếu danh sách 'scenes' hoặc 'beats'")

        if self.total_words <= 0:
            if self.scenes:
                self.total_words = sum(len(sc.speech.split()) for sc in self.scenes)
            elif self.beats:
                self.total_words = sum(
                    len(str(b.get("text") or b.get("text_anchor") or "").split())
                    for b in self.beats
                    if isinstance(b, dict)
                )

    def resolve_location_filename(self, loc_name: str) -> Optional[str]:
        """Trích xuất tên file tài nguyên từ bảng locations (hỗ trợ cả str và dict v3)."""
        if not self.locations or loc_name not in self.locations:
            return None
        val = self.locations[loc_name]
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for k in ("file", "asset", "path", "filename", "image", "video"):
                v = val.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    def ensure_scenes_from_beats(self) -> list[StoryScene]:
        """Nếu kịch bản v3 chỉ có beats mà chưa có scenes, tự động sinh scenes tương ứng để chạy TTS & phụ đề."""
        if self.scenes:
            return self.scenes
        if not self.beats:
            return []

        generated: list[StoryScene] = []
        for idx, b in enumerate(self.beats, start=1):
            if not isinstance(b, dict):
                continue
            b_id = int(b.get("id", idx))
            speech_txt = str(b.get("text") or b.get("text_anchor") or b.get("speech") or "").strip()
            if not speech_txt:
                continue

            loc_name = str(b.get("location", "")).strip()
            loc_meta = self.locations.get(loc_name, {}) if isinstance(self.locations, dict) else {}
            asset_type = "static_image"
            if isinstance(loc_meta, dict) and str(loc_meta.get("asset_type", "")).lower() in ("flow_video", "video", "clip"):
                asset_type = "flow_video"

            sfx_val = str(b.get("sfx", DEFAULT_SFX)).strip().lower()
            if sfx_val not in VALID_SFX:
                sfx_val = DEFAULT_SFX

            generated.append(
                StoryScene(
                    id=b_id,
                    speech=speech_txt,
                    source_note=str(b.get("fact_check") or b.get("source_note") or ""),
                    asset_type=asset_type,
                    sfx=sfx_val,
                )
            )
        self.scenes = generated
        return self.scenes

    def get_full_script(self) -> str:
        """Lấy toàn bộ kịch bản lời đọc dùng cho ElevenLabs /with-timestamps."""
        if self.full_script_for_elevenlabs:
            return self.full_script_for_elevenlabs
        if self.scenes:
            return " ".join(s.speech for s in self.scenes if s.speech.strip())
        if self.beats:
            return " ".join(
                str(b.get("text") or b.get("text_anchor") or "").strip()
                for b in self.beats
                if isinstance(b, dict) and (b.get("text") or b.get("text_anchor"))
            )
        return ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "title": self.title,
            "aspect_ratio": self.aspect_ratio,
            "frame_preset": self.frame_preset,
            "total_words": self.total_words,
            "style_prefix": self.style_prefix,
            "character_bible": self.character_bible,
            "full_script_for_elevenlabs": self.full_script_for_elevenlabs,
            "style_suffix": self.style_suffix,
            "bgm": self.bgm,
            "scenes": [s.to_dict() for s in self.scenes],
            "beats": self.beats,
            "locations": self.locations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Storyboard:
        if not isinstance(data, dict):
            raise DubberError("Dữ liệu storyboard phải là JSON object (dict)")

        scenes_raw = data.get("scenes")
        beats_raw = data.get("beats")

        if (scenes_raw is None or not isinstance(scenes_raw, list) or len(scenes_raw) == 0) and \
           (beats_raw is None or not isinstance(beats_raw, list) or len(beats_raw) == 0):
            raise DubberError("Storyboard thiếu danh sách 'scenes' hoặc 'beats'")

        scenes = [StoryScene.from_dict(s) for s in (scenes_raw or [])]
        beats = list(beats_raw) if isinstance(beats_raw, list) else []
        locations = dict(data.get("locations", {})) if isinstance(data.get("locations"), dict) else {}

        full_narration = str(
            data.get("full_script_for_elevenlabs")
            or data.get("full_narration")
            or ""
        ).strip()

        total_w = data.get("total_words", 0)
        try:
            total_w = int(total_w)
        except (ValueError, TypeError):
            total_w = 0

        return cls(
            title=data.get("title", "Untitled Story"),
            version=str(data.get("version", "1.0")),
            aspect_ratio=data.get("aspect_ratio", "9:16"),
            frame_preset=data.get("frame_preset", DEFAULT_FRAME_PRESET),
            total_words=total_w,
            style_prefix=data.get("style_prefix", ""),
            character_bible=data.get("character_bible", ""),
            full_script_for_elevenlabs=full_narration,
            style_suffix=data.get("style_suffix", ""),
            scenes=scenes,
            beats=beats,
            locations=locations,
            bgm=data.get("bgm", ""),
        )


def load_storyboard(path_or_data: Union[Path, str, dict]) -> Storyboard:
    """Tải và thẩm định storyboard từ file path, chuỗi JSON hoặc dict (hỗ trợ v1, v2.0 và v3.0)."""
    if isinstance(path_or_data, dict):
        return Storyboard.from_dict(path_or_data)

    target_path = Path(path_or_data)
    if not target_path.is_file():
        if isinstance(path_or_data, str) and path_or_data.strip().startswith("{"):
            try:
                data = json.loads(path_or_data)
                return Storyboard.from_dict(data)
            except json.JSONDecodeError as e:
                raise DubberError(f"Cú pháp JSON không hợp lệ: {e}") from e
        raise DubberError(f"Không tìm thấy file storyboard: {path_or_data}")

    try:
        content = target_path.read_text(encoding="utf-8-sig")
        data = json.loads(content)
    except UnicodeDecodeError as e:
        raise DubberError(f"Không thể đọc file storyboard (yêu cầu UTF-8): {e}") from e
    except json.JSONDecodeError as e:
        raise DubberError(f"Cú pháp JSON không hợp lệ trong file {target_path.name}: {e}") from e

    return Storyboard.from_dict(data)