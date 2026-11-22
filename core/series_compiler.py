"""Lõi Biên Tập Series YouTube 16:9 (Series Compiler) cho DubStudio — Đợt 1.

Cung cấp trọn bộ quy trình gộp series nhiều tập Story Mode (9:16 hoặc hỗn hợp)
thành 1 video tài liệu YouTube 16:9 (1920x1080 @ 30fps, -14 LUFS) hoàn chỉnh:
  1. Quét thư mục series & kiểm tra bản quyền tư liệu (`scan_series_folder`, `audit_series_licenses`).
  2. Lên khung 16:9 thông minh (Full-bleed vs Blurred Pillar-box) & Anti-AI chọn lọc (`build_youtube_16x9_scene_vf`).
  3. Cắt bỏ cảnh CTA giữa series an toàn (kèm đệm đuôi + afade) & đồng bộ tốc độ (`1.00–1.25x`).
  4. Vẽ Thẻ chương (Chapter Card) & End Credits tiếng Việt bằng Pillow (không dùng FFmpeg drawtext).
  5. Render phân mảnh từng tập -> FFmpeg concat -> trộn BGM 1.00x ducking -> loudnorm -14 LUFS -> xóa sạch metadata.
  6. Xuất 4 file đầu ra đăng YouTube (`<SeriesName>_Full_16x9.mp4`, `<SeriesName>_subtitles.srt`,
     `youtube_description.txt`, `compliance_report.json`).

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .anti_ai_filter import (
    build_ai_scene_depolish_vf,
    get_clean_metadata_flags,
    load_archival_scene_ids,
    normalize_anti_ai_level,
    probe_video_details,
)
from .audio_track import SR, fade_edges, read_wav, write_wav
from .cache import AudioCache, compute_cache_key
from .config import ROOT_DIR, WORK_ROOT, DubSettings, Keys
from .elevenlabs_alignment import align_scenes_with_words, chars_to_words
from .errors import DubberError
from .ffmpeg_utils import change_speed, probe, run_ffmpeg, to_wav
from .models import Segment, WordTiming
from .reporter import Reporter
from .sfx_engine import load_or_synthesize_sfx
from .story_pipeline import (
    ASSET_IMAGE_EXTS,
    ASSET_VIDEO_EXTS,
    _align_full_audio_with_whisper,
    _distribute_span,
    _prepare_bgm_audio,
    _scale_scenes_timings,
    find_scene_asset,
    generate_story_karaoke_ass,
    validate_story_assets,
)
from .story_schema import StoryScene, Storyboard, load_storyboard
from .subtitles import format_srt
from .tts import TTSEngine, ToneTTS, estimate_word_timings, make_engine

# ---------------------------------------------------------------------------
# Hằng số chuẩn xuất bản YouTube 16:9
# ---------------------------------------------------------------------------
YT_WIDTH = 1920
YT_HEIGHT = 1080
YT_FPS = 30
YT_AUDIO_SR = 48000

SMART_LANDSCAPE_RATIO = 1.45
VALID_LAYOUT_MODES: Tuple[str, ...] = ("smart", "pillarbox", "full_bleed")
VALID_SUBTITLE_MODES: Tuple[str, ...] = ("srt_only", "burn_and_srt")

MIN_SPEED_FACTOR = 1.00
MAX_SPEED_FACTOR = 1.25
DEFAULT_SPEED_FACTOR = 1.08

SAFE_CUT_TAIL_PAD = 0.09   # Đệm đuôi an toàn 0.08–0.10s khi cắt cảnh khỏi full_audio
SAFE_CUT_FADE_OUT = 0.06   # Fade-out mượt 0.06s tại ranh giới cắt tránh tiếng "bụp"

CTA_KEYWORDS: Tuple[str, ...] = (
    "tập sau",
    "phần tiếp theo",
    "tập tiếp theo",
    "theo dõi",
    "đăng ký",
    "bình luận",
    "đón xem",
    "kỳ sau",
    "hẹn gặp lại",
    "follow",
    "subscribe",
    "comment",
)

# Các mẫu kiểm tra rủi ro bản quyền/thương mại (dùng ranh giới chặt tránh false-positive)
_RISKY_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "NC / NonCommercial",
        re.compile(r"(?<![a-z0-9])(?:by-nc(?:-sa|-nd)?|cc-by-nc|nc|non[\s_-]?commercial)(?![a-z0-9])", re.I),
    ),
    (
        "ND / NoDerivatives",
        re.compile(r"(?<![a-z0-9])(?:by-nd|by-nc-nd|cc-by-nd|nd|no[\s_-]?derivatives?)(?![a-z0-9])", re.I),
    ),
    (
        "All rights reserved",
        re.compile(r"all\s+rights\s+reserved", re.I),
    ),
    (
        "Associated Press",
        re.compile(r"associated\s+press|(?<![a-z0-9])ap\s+(?:photo|images?)(?![a-z0-9])", re.I),
    ),
    (
        "Getty Images",
        re.compile(r"(?<![a-z0-9])getty(?:\s+images?)?(?![a-z0-9])", re.I),
    ),
    (
        "Reuters",
        re.compile(r"(?<![a-z0-9])reuters(?![a-z0-9])", re.I),
    ),
)

_CARD_FONT_CANDIDATES_BOLD: Tuple[str, ...] = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\tahomabd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_CARD_FONT_CANDIDATES_REGULAR: Tuple[str, ...] = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


# ---------------------------------------------------------------------------
# Dataclasses công khai (API Đợt 1 & chuẩn bị cho GUI Đợt 2)
# ---------------------------------------------------------------------------
@dataclass
class EpisodeEntry:
    """Thông tin một tập trong thư mục series."""

    episode_id: str
    folder: Path
    title: str
    chapter_title: str
    scene_count: int
    has_full_audio: bool
    missing_assets: List[int] = field(default_factory=list)
    archival_count: int = 0
    suggested_skip_scenes: List[int] = field(default_factory=list)
    assets_dir: Optional[Path] = None
    full_audio_path: Optional[Path] = None
    skip_scene_ids: Optional[List[int]] = None
    enabled: bool = True
    assets_16x9_dir: Optional[Path] = None
    assets_16x9_count: int = 0

    def resolve_skip_scenes(self, *, auto_skip_mid_cta: bool = True) -> List[int]:
        """Trả về danh sách scene_id bị cắt cuối cùng (ưu tiên cấu hình ghi đè)."""
        if self.skip_scene_ids is not None:
            return sorted({int(s) for s in self.skip_scene_ids})
        if auto_skip_mid_cta:
            return sorted({int(s) for s in self.suggested_skip_scenes})
        return []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "folder": str(self.folder),
            "title": self.title,
            "chapter_title": self.chapter_title,
            "scene_count": self.scene_count,
            "has_full_audio": self.has_full_audio,
            "missing_assets": list(self.missing_assets),
            "archival_count": self.archival_count,
            "suggested_skip_scenes": list(self.suggested_skip_scenes),
            "assets_dir": str(self.assets_dir) if self.assets_dir else None,
            "full_audio_path": str(self.full_audio_path) if self.full_audio_path else None,
            "skip_scene_ids": list(self.skip_scene_ids) if self.skip_scene_ids is not None else None,
            "enabled": self.enabled,
            "assets_16x9_dir": str(self.assets_16x9_dir) if self.assets_16x9_dir else None,
            "assets_16x9_count": self.assets_16x9_count,
        }


@dataclass
class ArchiveSourceItem:
    """Một nguồn tư liệu lịch sử/lưu trữ đã khử trùng lặp trên toàn series."""

    title: str
    author: str
    license: str
    source_url: str
    provider: str = ""
    year_or_date: str = ""
    sha256: str = ""
    note: str = ""
    episodes: List[str] = field(default_factory=list)
    scene_refs: List[str] = field(default_factory=list)
    risk_reasons: List[str] = field(default_factory=list)

    def credit_line(self) -> str:
        """Định dạng một dòng ghi công ngắn gọn, rõ ràng cho phần mô tả hoặc End Credits."""
        clean_title = re.sub(r"^File:", "", self.title or "Tư liệu lưu trữ", flags=re.I).strip()
        if "." in clean_title and len(clean_title.rsplit(".", 1)[-1]) <= 4:
            clean_title = clean_title.rsplit(".", 1)[0]
        parts = [clean_title]
        if self.author:
            parts.append(f"Tác giả/Nguồn: {self.author}")
        if self.license:
            parts.append(f"Giấy phép: {self.license}")
        line = " — ".join(parts)
        if self.source_url:
            line += f" ({self.source_url})"
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "license": self.license,
            "source_url": self.source_url,
            "provider": self.provider,
            "year_or_date": self.year_or_date,
            "sha256": self.sha256,
            "note": self.note,
            "episodes": list(self.episodes),
            "scene_refs": list(self.scene_refs),
            "risk_reasons": list(self.risk_reasons),
        }


@dataclass
class LicenseAuditReport:
    """Kết quả kiểm tra và phân nhóm bản quyền toàn series."""

    cc_attribution_required: List[ArchiveSourceItem] = field(default_factory=list)
    public_domain: List[ArchiveSourceItem] = field(default_factory=list)
    risky_warnings: List[ArchiveSourceItem] = field(default_factory=list)
    other_sources: List[ArchiveSourceItem] = field(default_factory=list)
    total_unique_sources: int = 0
    total_raw_entries: int = 0

    @property
    def has_risky_warnings(self) -> bool:
        return len(self.risky_warnings) > 0

    def all_safe_credits(self) -> List[ArchiveSourceItem]:
        return [*self.cc_attribution_required, *self.public_domain, *self.other_sources]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_raw_entries": self.total_raw_entries,
            "total_unique_sources": self.total_unique_sources,
            "has_risky_warnings": self.has_risky_warnings,
            "counts": {
                "cc_attribution_required": len(self.cc_attribution_required),
                "public_domain": len(self.public_domain),
                "risky_warnings": len(self.risky_warnings),
                "other_sources": len(self.other_sources),
            },
            "cc_attribution_required": [i.to_dict() for i in self.cc_attribution_required],
            "public_domain": [i.to_dict() for i in self.public_domain],
            "risky_warnings": [i.to_dict() for i in self.risky_warnings],
            "other_sources": [i.to_dict() for i in self.other_sources],
        }


@dataclass
class ChapterMarker:
    """Mốc chương thực tế sau khi render từng tập & thẻ chương."""

    chapter_index: int
    episode_id: str
    chapter_title: str
    start_sec: float
    episode_start_sec: float
    duration_sec: float
    card_duration_sec: float
    kept_scenes: List[int] = field(default_factory=list)
    skipped_scenes: List[int] = field(default_factory=list)
    archival_scenes: List[int] = field(default_factory=list)
    audio_source: str = "tts_per_scene"
    native_16x9_scenes: List[int] = field(default_factory=list)
    fallback_assets_scenes: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter_index": self.chapter_index,
            "episode_id": self.episode_id,
            "chapter_title": self.chapter_title,
            "start_sec": round(self.start_sec, 3),
            "episode_start_sec": round(self.episode_start_sec, 3),
            "duration_sec": round(self.duration_sec, 3),
            "card_duration_sec": round(self.card_duration_sec, 3),
            "youtube_timestamp": format_youtube_timestamp(self.start_sec),
            "kept_scenes": list(self.kept_scenes),
            "skipped_scenes": list(self.skipped_scenes),
            "archival_scenes": list(self.archival_scenes),
            "audio_source": self.audio_source,
            "native_16x9_scenes": list(self.native_16x9_scenes),
            "fallback_assets_scenes": list(self.fallback_assets_scenes),
            "native_16x9_count": len(self.native_16x9_scenes),
            "fallback_assets_count": len(self.fallback_assets_scenes),
        }


@dataclass
class SeriesCompileConfig:
    """Cấu hình biên tập Series YouTube 16:9."""

    series_name: Optional[str] = None
    series_title: Optional[str] = None
    output_dir: Optional[Union[Path, str]] = None
    layout_mode: str = "smart"
    subtitle_mode: str = "srt_only"
    subtitle_style: str = "karaoke_line"
    sub_font: str = "Arial"
    sub_font_size: int = 52
    sub_color: str = "#FFE000"
    watermark_text: Optional[str] = None
    watermark_pos: str = "top_left"
    watermark_opacity: int = 65
    speed_factor: float = DEFAULT_SPEED_FACTOR
    anti_ai_level: Union[str, bool, None] = "light"
    auto_skip_mid_cta: bool = True
    prefer_assets_16x9: bool = True
    episode_skip_overrides: Dict[str, List[int]] = field(default_factory=dict)
    include_chapter_cards: bool = True
    chapter_card_duration: float = 2.5
    include_end_credits: bool = True
    end_credits_duration: float = 4.5
    bgm_path: Optional[Union[Path, str]] = None
    bgm_volume: float = 0.28
    sfx_volume: float = 0.80
    scene_pad: float = 0.20
    loudnorm_youtube: bool = True
    use_whisper_align: bool = True
    whisper_model: str = "small"
    crf: int = 20
    preset: str = "veryfast"
    keep_temp: bool = False
    work_root: Optional[Union[Path, str]] = None
    channel_name: str = ""
    intro_description: str = ""


@dataclass
class SeriesCompileResult:
    """Kết quả trọn bộ 4 file xuất bản YouTube và thông số kiểm tra."""

    video_path: Path
    srt_path: Path
    description_path: Path
    report_path: Path
    chapters: List[ChapterMarker]
    license_report: LicenseAuditReport
    total_duration_sec: float
    width: int
    height: int
    fps: float
    loudnorm_applied: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_path": str(self.video_path),
            "srt_path": str(self.srt_path),
            "description_path": str(self.description_path),
            "report_path": str(self.report_path),
            "total_duration_sec": round(self.total_duration_sec, 3),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "loudnorm_applied": self.loudnorm_applied,
            "chapters": [c.to_dict() for c in self.chapters],
            "license_report": self.license_report.to_dict(),
        }


# ---------------------------------------------------------------------------
# 1. Quét Series & Kiểm tra Bản quyền
# ---------------------------------------------------------------------------
def _natural_sort_key(text: str) -> Tuple[Any, ...]:
    parts = re.split(r"(\d+)", str(text))
    return tuple((0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts if p)


def _strip_vi_accents(text: str) -> str:
    s = unicodedata.normalize("NFC", str(text)).lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def _build_chapter_title(raw_title: str, episode_id: str, index: int) -> str:
    """Rút gọn tên tập thành tiêu đề chương gọn đẹp."""
    source = (raw_title or episode_id or f"Phần {index:02d}").strip()
    cleaned = re.sub(r"[_\-]+", " ", source).strip()
    # Bỏ tiền tố series/tập thường gặp kiểu 'Dien Bien Phu Tap 01 Cai Bay Long Chao' hoặc 'Tập 1: ...'
    stripped = re.sub(
        r"^(?:.*?\b(?:tap|tập|ep|episode|phan|phần)\s*\d+\s*[:.\-–—]?\s*)",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    if stripped and len(stripped) >= 3:
        return stripped
    # Thử rút từ episode_id nếu raw_title bị trống sau cắt tiền tố
    ep_num_m = re.search(r"(?:tap|tập|ep|episode|phan|phần)[_\s-]*(\d+)", f"{source} {episode_id}", re.I)
    if ep_num_m:
        return f"Tập {int(ep_num_m.group(1)):02d}"
    return cleaned or f"Tập {index:02d}"


def _is_cta_speech(speech: str) -> bool:
    """Nhận diện câu thoại cuối tập có chứa CTA chuyển tập / kêu gọi tương tác."""
    if not speech:
        return False
    low = unicodedata.normalize("NFC", speech).lower()
    no_acc = _strip_vi_accents(speech)
    for kw in CTA_KEYWORDS:
        if kw in low or _strip_vi_accents(kw) in no_acc:
            return True
    return False


def _resolve_episode_paths(ep_dir: Path) -> Tuple[Path, Optional[Path], Optional[Path]]:
    """Tìm thư mục assets/, assets_16x9/ (nếu có) và file full_audio (.mp3/.wav) của một tập."""
    assets_dir = ep_dir / "assets" if (ep_dir / "assets").is_dir() else ep_dir
    cand_16x9 = ep_dir / "assets_16x9"
    assets_16x9_dir = cand_16x9.resolve() if cand_16x9.is_dir() else None
    full_audio: Optional[Path] = None
    for candidate_dir in (assets_dir, ep_dir):
        for name in ("full_audio.mp3", "full_audio.wav"):
            p = candidate_dir / name
            if p.is_file() and p.stat().st_size > 0:
                full_audio = p.resolve()
                break
        if full_audio is not None:
            break
    return assets_dir, assets_16x9_dir, full_audio


def _read_storyboard_series_extras(sb_path: Path) -> Tuple[str, Dict[int, bool]]:
    """Đọc các trường tùy chọn V5.4 (`chapter_title` top-level và `is_cta` trên từng cảnh)."""
    try:
        raw = json.loads(sb_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return "", {}
    if not isinstance(raw, dict):
        return "", {}

    ch_title = str(raw.get("chapter_title") or "").strip()
    cta_flags: Dict[int, bool] = {}
    for idx, sc in enumerate(raw.get("scenes") or [], start=1):
        if isinstance(sc, dict) and "is_cta" in sc and isinstance(sc.get("is_cta"), bool):
            sid = sc.get("id", idx)
            if isinstance(sid, int) and not isinstance(sid, bool):
                cta_flags[sid] = bool(sc["is_cta"])
    return ch_title, cta_flags


def _resolve_episode_scene_assets(
    sb: Storyboard,
    assets_dir: Path,
    assets_16x9_dir: Optional[Path],
    *,
    prefer_assets_16x9: bool = True,
) -> Tuple[Dict[int, Path], List[int], List[int], List[int]]:
    """Tra cứu media từng cảnh: ưu tiên `assets_16x9/` (nếu bật) rồi fallback về `assets/`."""
    sb.ensure_scenes_from_beats()
    fallback_map, _ = validate_story_assets(sb, assets_dir) if assets_dir.is_dir() else ({}, [])

    resolved: Dict[int, Path] = {}
    missing: List[int] = []
    native_16x9_ids: List[int] = []
    fallback_ids: List[int] = []

    use_16x9 = bool(prefer_assets_16x9 and assets_16x9_dir is not None and assets_16x9_dir.is_dir())
    for sc in sb.scenes:
        if use_16x9 and assets_16x9_dir is not None:
            cand_169 = find_scene_asset(assets_16x9_dir, sc.id)
            if cand_169 is not None:
                resolved[sc.id] = cand_169
                native_16x9_ids.append(sc.id)
                continue

        cand_fb = fallback_map.get(sc.id)
        if cand_fb is not None:
            resolved[sc.id] = cand_fb
            fallback_ids.append(sc.id)
        else:
            missing.append(sc.id)

    return resolved, missing, native_16x9_ids, fallback_ids


def scan_series_folder(series_dir: Union[Path, str]) -> List[EpisodeEntry]:
    """Quét thư mục series, sắp xếp tự nhiên các tập có storyboard.json và gợi ý cắt CTA giữa series."""
    root = Path(series_dir)
    if not root.is_dir():
        raise DubberError(f"Thư mục series không tồn tại: {root}")

    ep_dirs = [
        p for p in root.iterdir()
        if p.is_dir() and (p / "storyboard.json").is_file()
    ]
    ep_dirs.sort(key=lambda p: _natural_sort_key(p.name))
    if not ep_dirs:
        raise DubberError(f"Không tìm thấy tập nào chứa storyboard.json trong: {root}")

    loaded: List[Tuple[Path, Storyboard, Path, Optional[Path], int, Optional[Path], List[int], int, str, Dict[int, bool]]] = []
    for ep_dir in ep_dirs:
        sb_path = ep_dir / "storyboard.json"
        sb = load_storyboard(sb_path)
        sb.ensure_scenes_from_beats()
        explicit_ch_title, cta_flags = _read_storyboard_series_extras(sb_path)
        assets_dir, assets_16x9_dir, full_audio = _resolve_episode_paths(ep_dir)
        _, missing, native_169_ids, _ = _resolve_episode_scene_assets(
            sb, assets_dir, assets_16x9_dir, prefer_assets_16x9=True
        )
        archival_ids = (
            load_archival_scene_ids(ep_dir)
            | load_archival_scene_ids(assets_dir)
            | (load_archival_scene_ids(assets_16x9_dir) if assets_16x9_dir else set())
        )
        loaded.append(
            (
                ep_dir,
                sb,
                assets_dir,
                assets_16x9_dir,
                len(native_169_ids),
                full_audio,
                missing,
                len(archival_ids),
                explicit_ch_title,
                cta_flags,
            )
        )

    entries: List[EpisodeEntry] = []
    total_eps = len(loaded)
    for idx, (
        ep_dir,
        sb,
        assets_dir,
        assets_16x9_dir,
        count_16x9,
        full_audio,
        missing,
        archival_count,
        explicit_ch_title,
        cta_flags,
    ) in enumerate(loaded, start=1):
        is_last = idx == total_eps
        suggested_skip: List[int] = []
        if not is_last and len(sb.scenes) > 1:
            explicit_skip = [sc.id for sc in sb.scenes if cta_flags.get(sc.id) is True]
            last_scene = sb.scenes[-1]
            if last_scene.id not in explicit_skip and cta_flags.get(last_scene.id) is not False:
                if _is_cta_speech(last_scene.speech):
                    explicit_skip.append(last_scene.id)
            if len(explicit_skip) < len(sb.scenes):
                suggested_skip = sorted(set(explicit_skip))

        entries.append(
            EpisodeEntry(
                episode_id=ep_dir.name,
                folder=ep_dir.resolve(),
                title=sb.title or ep_dir.name,
                chapter_title=explicit_ch_title or _build_chapter_title(sb.title, ep_dir.name, idx),
                scene_count=len(sb.scenes),
                has_full_audio=full_audio is not None,
                missing_assets=missing,
                archival_count=archival_count,
                suggested_skip_scenes=suggested_skip,
                assets_dir=assets_dir.resolve(),
                full_audio_path=full_audio,
                assets_16x9_dir=assets_16x9_dir,
                assets_16x9_count=count_16x9,
            )
        )
    return entries


def _extract_archive_records(ep_dir: Path, assets_dir: Optional[Path]) -> List[Tuple[str, Dict[str, Any]]]:
    """Đọc các bản ghi tư liệu từ archive_sources.json của một tập."""
    sources_path: Optional[Path] = None
    for cand in (ep_dir / "archive_sources.json", (assets_dir / "archive_sources.json") if assets_dir else None):
        if cand is not None and cand.is_file():
            sources_path = cand
            break
    if sources_path is None:
        return []

    try:
        raw_text = sources_path.read_text(encoding="utf-8-sig").strip()
        if not raw_text:
            return []
        data = json.loads(raw_text)
    except (OSError, ValueError):
        return []

    records: List[Tuple[str, Dict[str, Any]]] = []

    def add_mapping(mapping: Dict[str, Any]) -> None:
        for k, v in mapping.items():
            if isinstance(v, dict):
                records.append((str(v.get("scene_id") or v.get("scene") or k), v))

    def add_seq(seq: Sequence[Any]) -> None:
        for idx, v in enumerate(seq, start=1):
            if isinstance(v, dict):
                records.append((str(v.get("scene_id") or v.get("scene") or v.get("id") or idx), v))

    if isinstance(data, list):
        add_seq(data)
    elif isinstance(data, dict):
        has_container = False
        for key in ("assets", "items", "scenes"):
            sub = data.get(key)
            if isinstance(sub, dict):
                has_container = True
                add_mapping(sub)
            elif isinstance(sub, list):
                has_container = True
                add_seq(sub)
        if not has_container:
            add_mapping(data)

    return records


def _detect_license_risks(raw: Dict[str, Any]) -> List[str]:
    """Kiểm tra từ khóa cấm thương mại hoặc hãng thông tấn trên bản ghi nguồn."""
    haystack = " | ".join(
        str(raw.get(k) or "").strip()
        for k in ("license", "source_url", "url", "provider", "author", "title", "note")
        if raw.get(k)
    )
    if not haystack:
        return []
    reasons: List[str] = []
    for label, pattern in _RISKY_PATTERNS:
        if pattern.search(haystack):
            reasons.append(label)
    return reasons


def _classify_safe_license(license_str: str) -> str:
    """Phân loại giấy phép không có cờ rủi ro sang nhóm tương ứng."""
    lic = (license_str or "").strip().lower()
    if (
        "public domain" in lic
        or "cc0" in lic
        or "no known copyright" in lic
        or re.search(r"(?<![a-z0-9])pd(?![a-z0-9])", lic)
    ):
        return "public_domain"
    if re.search(r"cc[\s_-]*by", lic) or "creative commons attribution" in lic:
        return "cc_attribution_required"
    return "other"


def audit_series_licenses(
    episodes: Sequence[Union[EpisodeEntry, Path, str]],
) -> LicenseAuditReport:
    """Đọc toàn bộ `archive_sources.json` của các tập, khử trùng lặp và phân nhóm bản quyền."""
    unique_items: List[ArchiveSourceItem] = []
    sha_index: Dict[str, int] = {}
    url_index: Dict[str, int] = {}
    fallback_index: Dict[Tuple[str, str, str], int] = {}
    raw_count = 0

    for ep in episodes:
        if isinstance(ep, EpisodeEntry):
            ep_id = ep.episode_id
            ep_dir = ep.folder
            assets_dir = ep.assets_dir or (ep_dir / "assets" if (ep_dir / "assets").is_dir() else ep_dir)
        else:
            ep_dir = Path(ep)
            ep_id = ep_dir.name
            assets_dir = ep_dir / "assets" if (ep_dir / "assets").is_dir() else ep_dir

        for scene_key, rec in _extract_archive_records(ep_dir, assets_dir):
            raw_count += 1
            sha = str(rec.get("sha256") or "").strip().lower()
            raw_url = str(rec.get("source_url") or rec.get("url") or "").strip()
            norm_url = raw_url.lower().rstrip("/")
            title = str(rec.get("title") or rec.get("asset_file") or f"Scene {scene_key}").strip()
            author = str(rec.get("author") or "").strip()
            lic = str(rec.get("license") or "").strip()
            provider = str(rec.get("provider") or "").strip()
            year_or_date = str(rec.get("year_or_date") or rec.get("year") or rec.get("date") or "").strip()
            note = str(rec.get("note") or "").strip()

            existing_idx: Optional[int] = None
            if sha and sha in sha_index:
                existing_idx = sha_index[sha]
            elif norm_url and norm_url in url_index:
                existing_idx = url_index[norm_url]
            elif not sha and not norm_url:
                fb_key = (title.lower(), author.lower(), lic.lower())
                existing_idx = fallback_index.get(fb_key)

            risks = _detect_license_risks(rec)
            scene_ref = f"{ep_id}#{scene_key}"

            if existing_idx is not None:
                item = unique_items[existing_idx]
                if not item.sha256 and sha:
                    item.sha256 = sha
                if not item.source_url and raw_url:
                    item.source_url = raw_url
                if not item.author and author:
                    item.author = author
                if not item.license and lic:
                    item.license = lic
                if not item.provider and provider:
                    item.provider = provider
                if not item.year_or_date and year_or_date:
                    item.year_or_date = year_or_date
                if ep_id not in item.episodes:
                    item.episodes.append(ep_id)
                if scene_ref not in item.scene_refs:
                    item.scene_refs.append(scene_ref)
                for r in risks:
                    if r not in item.risk_reasons:
                        item.risk_reasons.append(r)
            else:
                item = ArchiveSourceItem(
                    title=title,
                    author=author,
                    license=lic,
                    source_url=raw_url,
                    provider=provider,
                    year_or_date=year_or_date,
                    sha256=sha,
                    note=note,
                    episodes=[ep_id],
                    scene_refs=[scene_ref],
                    risk_reasons=risks,
                )
                existing_idx = len(unique_items)
                unique_items.append(item)

            if sha:
                sha_index[sha] = existing_idx
            if norm_url:
                url_index[norm_url] = existing_idx
            if not sha and not norm_url:
                fallback_index[(title.lower(), author.lower(), lic.lower())] = existing_idx

    report = LicenseAuditReport(
        total_unique_sources=len(unique_items),
        total_raw_entries=raw_count,
    )
    for item in unique_items:
        if item.risk_reasons:
            report.risky_warnings.append(item)
        else:
            bucket = _classify_safe_license(item.license)
            if bucket == "cc_attribution_required":
                report.cc_attribution_required.append(item)
            elif bucket == "public_domain":
                report.public_domain.append(item)
            else:
                report.other_sources.append(item)
    return report


# ---------------------------------------------------------------------------
# 2. Lên khung 16:9 Thông minh & Anti-AI Chọn lọc
# ---------------------------------------------------------------------------
def _even(val: int, minimum: int = 2) -> int:
    v = max(minimum, int(val))
    return v - (v % 2)


def probe_media_dimensions(path: Union[Path, str]) -> Tuple[int, int]:
    """Đọc kích thước thực (width, height) của file ảnh hoặc video."""
    p = Path(path)
    if p.suffix.lower() in ASSET_IMAGE_EXTS:
        try:
            with Image.open(p) as im:
                w, h = im.size
                if w > 0 and h > 0:
                    return int(w), int(h)
        except Exception:
            pass
    info = probe_video_details(p)
    return max(2, info.width), max(2, info.height)


def resolve_youtube_16x9_layout(
    src_w: int,
    src_h: int,
    layout_mode: str = "smart",
) -> str:
    """Quyết định kiểu bố cục 16:9 (`full_bleed` hoặc `pillarbox`) từ kích thước gốc."""
    mode = str(layout_mode or "smart").strip().lower()
    if mode not in VALID_LAYOUT_MODES:
        raise DubberError(
            f"layout_mode '{layout_mode}' không hợp lệ (chấp nhận: {', '.join(VALID_LAYOUT_MODES)})."
        )
    aspect = float(max(1, src_w)) / float(max(1, src_h))
    if mode == "full_bleed":
        return "full_bleed"
    if mode == "pillarbox":
        return "full_bleed" if abs(aspect - (16.0 / 9.0)) <= 0.03 else "pillarbox"
    return "full_bleed" if aspect >= SMART_LANDSCAPE_RATIO else "pillarbox"


def _build_ken_burns_zoompan(
    motion: str,
    duration: float,
    width: int,
    height: int,
    fps: int = YT_FPS,
    max_zoom: float = 1.08,
) -> str:
    """Tạo filter zoompan nhẹ cho khung kích thước (width x height)."""
    d = max(1, int(math.ceil(max(0.1, duration) * fps)))
    delta = max(0.02, max_zoom - 1.0)
    step = delta / max(d, 1)
    m = (motion or "zoom_in").strip().lower()

    if m == "zoom_out":
        return (
            f"zoompan=z='if(eq(on,0),{max_zoom:.3f},max(1.0,pzoom-{step:.6f}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps}"
        )
    if m == "pan_left":
        return (
            f"zoompan=z='{max_zoom:.3f}':"
            f"x='(1.0-on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps}"
        )
    if m == "pan_right":
        return (
            f"zoompan=z='{max_zoom:.3f}':"
            f"x='(on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps}"
        )
    if m == "static":
        return (
            f"zoompan=z='1.0':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps}"
        )
    return (
        f"zoompan=z='min(pzoom+{step:.6f},{max_zoom:.3f})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={d}:s={width}x{height}:fps={fps}"
    )


def _compute_pillarbox_fg_size(
    src_w: int,
    src_h: int,
    out_w: int = YT_WIDTH,
    out_h: int = YT_HEIGHT,
    border_px: int = 4,
) -> Tuple[int, int]:
    """Tính kích thước tiền cảnh chẵn pixel giữ nguyên tỉ lệ gốc, chừa chỗ cho viền tối."""
    max_w = _even(out_w - 2 * border_px)
    max_h = _even(out_h - 2 * border_px)
    aspect = float(max(1, src_w)) / float(max(1, src_h))
    if aspect <= (float(max_w) / float(max_h)):
        fg_h = max_h
        fg_w = max(2, min(max_w, _even(int(round(fg_h * aspect)))))
    else:
        fg_w = max_w
        fg_h = max(2, min(max_h, _even(int(round(fg_w / aspect)))))
    return fg_w, fg_h


def build_youtube_16x9_scene_vf(
    asset_path: Optional[Union[Path, str]] = None,
    duration: float = 3.0,
    *,
    motion: str = "zoom_in",
    layout_mode: str = "smart",
    is_video: Optional[bool] = None,
    is_archival: bool = False,
    anti_ai_level: Union[str, bool, None] = "off",
    src_size: Optional[Tuple[int, int]] = None,
    fps: int = YT_FPS,
    out_w: int = YT_WIDTH,
    out_h: int = YT_HEIGHT,
) -> str:
    """Tạo chuỗi filter video FFmpeg 16:9 thông minh kèm Anti-AI chọn lọc trên lớp AI.

    - `smart`: tỉ lệ gốc `w/h >= 1.45` -> full-bleed `1920x1080`; ảnh dọc/vuông -> Blurred Pillar-box.
    - `pillarbox`: luôn dùng khung tài liệu nền mờ cho ảnh không đúng 16:9.
    - `full_bleed`: luôn phủ kín `1920x1080`, ưu tiên giữ headroom (`0.30`).
    - `is_archival=True`: miễn 100% lọc khử bóng Anti-AI để bảo toàn ảnh tư liệu lịch sử thật.
    """
    if src_size is not None:
        src_w, src_h = max(2, int(src_size[0])), max(2, int(src_size[1]))
    elif asset_path is not None:
        src_w, src_h = probe_media_dimensions(asset_path)
    else:
        raise DubberError("Cần cung cấp asset_path hoặc src_size cho build_youtube_16x9_scene_vf.")

    if is_video is None and asset_path is not None:
        is_video = Path(asset_path).suffix.lower() in ASSET_VIDEO_EXTS
    is_vid = bool(is_video)

    effective_layout = resolve_youtube_16x9_layout(src_w, src_h, layout_mode)
    lv = normalize_anti_ai_level(anti_ai_level)
    apply_depolish = (not is_archival) and (lv != "off")

    if effective_layout == "full_bleed":
        parts = [
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase",
            f"crop={out_w}:{out_h}:(iw-{out_w})/2:(ih-{out_h})*0.30",
            "setsar=1",
        ]
        if is_vid:
            parts.append(f"fps={fps}")
        else:
            parts.append(_build_ken_burns_zoompan(motion, duration, out_w, out_h, fps=fps, max_zoom=1.08))
            parts.append("setsar=1")
        if apply_depolish:
            depolish = build_ai_scene_depolish_vf(lv, width=out_w, height=out_h)
            if depolish:
                parts.append(depolish)
        parts.append("format=yuv420p")
        return ",".join(parts)

    # Nhánh Blurred Pillar-box giữ trọn 100% tỉ lệ ảnh dọc/vuông
    border_px = 4
    fg_w, fg_h = _compute_pillarbox_fg_size(src_w, src_h, out_w=out_w, out_h=out_h, border_px=border_px)
    bg_chain = (
        "[bg]scale=480:270:force_original_aspect_ratio=increase,"
        "crop=480:270,setsar=1,"
        "gblur=sigma=12,"
        f"scale={out_w}:{out_h}:flags=bilinear,setsar=1,"
        f"eq=brightness=-0.22:saturation=0.75,fps={fps}[bg_out]"
    )

    fg_steps: List[str] = [f"scale={fg_w}:{fg_h}:flags=lanczos", "setsar=1"]
    if is_vid:
        fg_steps.append(f"fps={fps}")
    else:
        fg_steps.append(_build_ken_burns_zoompan(motion, duration, fg_w, fg_h, fps=fps, max_zoom=1.08))
        fg_steps.append("setsar=1")
    if apply_depolish:
        depolish = build_ai_scene_depolish_vf(lv, width=fg_w, height=fg_h)
        if depolish:
            fg_steps.append(depolish)

    pad_w = fg_w + 2 * border_px
    pad_h = fg_h + 2 * border_px
    fg_steps.append(f"pad={pad_w}:{pad_h}:{border_px}:{border_px}:color=0x141414")
    fg_chain = f"[fg]{','.join(fg_steps)}[fg_out]"

    return (
        f"split=2[bg][fg];"
        f"{bg_chain};"
        f"{fg_chain};"
        f"[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,format=yuv420p"
    )


# ---------------------------------------------------------------------------
# 3. Cắt cảnh CTA an toàn & Đồng bộ tốc độ
# ---------------------------------------------------------------------------
def _normalize_scene_words(scenes: Sequence[StoryScene]) -> None:
    """Bảo đảm sc.words luôn là List[WordTiming] để tương thích _scale_scenes_timings."""
    for sc in scenes:
        raw_words = getattr(sc, "words", None) or []
        norm: List[WordTiming] = []
        for w in raw_words:
            if isinstance(w, WordTiming):
                norm.append(w)
            elif isinstance(w, dict):
                norm.append(WordTiming.from_dict(w))
        sc.words = norm


def _find_alignment_json(ep: EpisodeEntry) -> Optional[Path]:
    candidates = [
        (ep.assets_dir / "alignment.json") if ep.assets_dir else None,
        ep.folder / "alignment.json",
    ]
    for cand in candidates:
        if cand is not None and cand.is_file():
            return cand
    return None


def _populate_full_audio_alignments(
    raw_voice_wav: Path,
    scenes: List[StoryScene],
    raw_total_duration: float,
    ep: EpisodeEntry,
    config: SeriesCompileConfig,
    rep: Reporter,
) -> None:
    """Gán mốc thời gian 1.00x cho toàn bộ scenes từ alignment.json, Whisper hoặc nội suy tỉ lệ."""
    align_path = _find_alignment_json(ep)
    if align_path is not None:
        try:
            align_dict = json.loads(align_path.read_text(encoding="utf-8-sig"))
            if isinstance(align_dict, dict) and "characters" in align_dict:
                words_list = chars_to_words(align_dict)
                align_scenes_with_words(scenes, words_list)
            elif isinstance(align_dict, dict) and isinstance(align_dict.get("words"), list):
                align_scenes_with_words(scenes, align_dict["words"])
        except Exception as e:
            rep.log(f"⚠️ Bỏ qua alignment.json lỗi ở {ep.episode_id}: {e}", "warn")

    _normalize_scene_words(scenes)

    if not any(sc.words for sc in scenes) and config.use_whisper_align:
        cache = AudioCache()
        _align_full_audio_with_whisper(
            raw_voice_wav,
            scenes,
            raw_total_duration,
            model_size=config.whisper_model or "small",
            reporter=rep,
            cache_dir=cache.cache_dir,
        )
        _normalize_scene_words(scenes)

    # Nếu chưa có mốc từ (khi tắt Whisper và không có alignment.json), chia đều theo độ dài thoại
    if not any(sc.words for sc in scenes):
        has_explicit_spans = all((sc.duration_sec > 0 or sc.end_time > sc.start_time) for sc in scenes)
        if has_explicit_spans and len(scenes) > 0:
            raw_sum = sum(
                (sc.end_time - sc.start_time) if sc.end_time > sc.start_time else sc.duration_sec
                for sc in scenes
            )
            scale = (raw_total_duration / raw_sum) if raw_sum > 0 else 1.0
            cursor = 0.0
            for idx, sc in enumerate(scenes):
                orig_d = (sc.end_time - sc.start_time) if sc.end_time > sc.start_time else sc.duration_sec
                dur = max(0.2, orig_d * scale)
                if idx == len(scenes) - 1:
                    dur = max(0.2, raw_total_duration - cursor)
                sc.start_time = round(cursor, 3)
                sc.end_time = round(cursor + dur, 3)
                sc.duration_sec = round(dur, 3)
                est = estimate_word_timings(sc.speech, dur)
                sc.words = [w.scale_and_shift(speed=1.0, offset=sc.start_time) for w in est]
                cursor += dur
        else:
            tokens_by_scene = [(idx, tok) for idx, sc in enumerate(scenes) for tok in sc.speech.split() if tok.strip()]
            spans = _distribute_span([t for _, t in tokens_by_scene], 0.0, raw_total_duration)
            per_sc: Dict[int, List[WordTiming]] = {i: [] for i in range(len(scenes))}
            for (idx, tok), (st, en) in zip(tokens_by_scene, spans):
                per_sc[idx].append(WordTiming(word=tok, start=round(st, 3), end=round(en, 3)))
            for idx, sc in enumerate(scenes):
                sw = per_sc[idx]
                if sw:
                    sc.words = sw
                    sc.start_time = 0.0 if idx == 0 else sw[0].start
                    sc.end_time = sw[-1].end
                    sc.duration_sec = round(max(0.2, sc.end_time - sc.start_time), 3)

    if scenes:
        scenes[0].start_time = 0.0
        for i in range(len(scenes) - 1):
            if scenes[i].end_time <= scenes[i].start_time:
                scenes[i].end_time = max(scenes[i].start_time + 0.3, scenes[i + 1].start_time)
            scenes[i].duration_sec = round(max(0.2, scenes[i].end_time - scenes[i].start_time), 3)
        if scenes[-1].end_time <= scenes[-1].start_time:
            scenes[-1].end_time = max(scenes[-1].start_time + 0.3, raw_total_duration)
        scenes[-1].duration_sec = round(max(0.2, scenes[-1].end_time - scenes[-1].start_time), 3)


def _cut_and_reassemble_full_audio(
    raw_voice_wav: Path,
    scenes: List[StoryScene],
    skip_set: set[int],
    raw_total_duration: float,
    work_dir: Path,
) -> Tuple[Path, List[StoryScene], float]:
    """Cắt các đoạn scene giữ lại khỏi full_audio kèm đệm đuôi ~0.09s và afade 0.06s."""
    kept_indices = [i for i, sc in enumerate(scenes) if sc.id not in skip_set]
    if not kept_indices:
        raise DubberError("Không thể cắt toàn bộ các cảnh của một tập.")

    if not skip_set or len(kept_indices) == len(scenes):
        return raw_voice_wav, list(scenes), raw_total_duration

    # Gom các chỉ số liên tiếp thành từng block để giảm tối đa điểm nối
    runs: List[List[int]] = []
    for idx in kept_indices:
        if not runs or idx != runs[-1][-1] + 1:
            runs.append([idx])
        else:
            runs[-1].append(idx)

    cut_parts: List[Path] = []
    kept_scenes: List[StoryScene] = []
    new_cursor = 0.0

    for run_idx, block in enumerate(runs):
        first_i = block[0]
        last_i = block[-1]
        first_sc = scenes[first_i]
        last_sc = scenes[last_i]

        first_words = getattr(first_sc, "words", None) or []
        first_speech_st = float(first_words[0].start) if first_words else float(first_sc.start_time)
        cut_start = 0.0 if first_i == 0 else max(0.0, min(float(first_sc.start_time), first_speech_st) - 0.02)
        if last_i == len(scenes) - 1:
            cut_end = raw_total_duration
        else:
            next_sc = scenes[last_i + 1]
            next_words = getattr(next_sc, "words", None) or []
            next_speech_st = float(next_words[0].start) if next_words else float(next_sc.start_time)
            next_boundary = max(float(next_sc.start_time), next_speech_st)
            speech_end = float(last_sc.words[-1].end) if last_sc.words else float(last_sc.end_time)
            padded_end = speech_end + SAFE_CUT_TAIL_PAD
            # Bảo đảm đệm đuôi không bao giờ tràn sang lời cảnh kế tiếp bị cắt
            upper_bound = next_boundary if next_boundary >= speech_end else raw_total_duration
            cut_end = min(raw_total_duration, max(speech_end, min(padded_end, upper_bound)))

        seg_dur = max(0.08, cut_end - cut_start)
        part_wav = work_dir / f"cut_part_{run_idx:03d}.wav"

        af_parts: List[str] = []
        if cut_start > 0.0:
            fade_in_d = min(0.02, seg_dur * 0.25)
            af_parts.append(f"afade=t=in:st=0:d={fade_in_d:.4f}")
        if cut_end < raw_total_duration - 0.005:
            fade_out_d = min(SAFE_CUT_FADE_OUT, seg_dur * 0.4)
            fade_out_st = max(0.0, seg_dur - fade_out_d)
            af_parts.append(f"afade=t=out:st={fade_out_st:.4f}:d={fade_out_d:.4f}")

        cmd: List[Any] = [
            "-ss", f"{cut_start:.4f}",
            "-t", f"{seg_dur:.4f}",
            "-i", str(raw_voice_wav),
        ]
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]
        cmd += ["-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(part_wav)]
        run_ffmpeg(cmd)

        actual_part_dur = probe(part_wav).duration
        cut_parts.append(part_wav)

        for local_pos, orig_i in enumerate(block):
            sc = scenes[orig_i]
            orig_st = float(sc.start_time)
            if local_pos < len(block) - 1:
                orig_en = float(scenes[block[local_pos + 1]].start_time)
            else:
                orig_en = cut_start + actual_part_dur

            shifted_st = max(0.0, (orig_st - cut_start) + new_cursor)
            shifted_en = max(shifted_st + 0.1, (orig_en - cut_start) + new_cursor)

            shifted_words = [
                WordTiming(
                    word=w.word,
                    start=round(max(shifted_st, (w.start - cut_start) + new_cursor), 4),
                    end=round(max(shifted_st + 0.03, (w.end - cut_start) + new_cursor), 4),
                )
                for w in (sc.words or [])
            ]
            sc_copy = StoryScene(
                id=sc.id,
                speech=sc.speech,
                source_note=sc.source_note,
                asset_type=sc.asset_type,
                image_prompt=sc.image_prompt,
                motion_prompt=sc.motion_prompt,
                motion=sc.motion,
                ken_burns=sc.ken_burns,
                sfx=sc.sfx,
                on_screen_text=sc.on_screen_text,
                start_time=round(shifted_st, 3),
                end_time=round(shifted_en, 3),
                duration_sec=round(max(0.2, shifted_en - shifted_st), 3),
                words=shifted_words,
            )
            kept_scenes.append(sc_copy)

        new_cursor += actual_part_dur

    if len(cut_parts) == 1:
        merged_wav = cut_parts[0]
    else:
        merged_wav = work_dir / "voice_trimmed_raw.wav"
        concat_txt = work_dir / "voice_cut_concat.txt"
        concat_txt.write_text(
            "".join(f"file '{p.name}'\n" for p in cut_parts),
            encoding="utf-8",
        )
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_txt.name, "-c", "copy", str(merged_wav)], cwd=work_dir)

    final_raw_dur = probe(merged_wav).duration
    if kept_scenes:
        kept_scenes[0].start_time = 0.0
    return merged_wav, kept_scenes, final_raw_dur


def _prepare_episode_audio_and_timeline(
    ep: EpisodeEntry,
    sb: Storyboard,
    skip_set: set[int],
    speed_factor: float,
    ep_work: Path,
    config: SeriesCompileConfig,
    rep: Reporter,
    *,
    tts_engine: Optional[TTSEngine] = None,
    cache: Optional[AudioCache] = None,
    settings: Optional[DubSettings] = None,
    keys: Optional[Keys] = None,
) -> Tuple[List[StoryScene], List[float], List[float], List[Segment], Path, float, str]:
    """Chuẩn bị timeline cảnh giữ lại, tốc độ đồng bộ và audio 48kHz stereo (voice + sfx)."""
    apply_speed = abs(speed_factor - 1.0) > 1e-3
    voice_wav = ep_work / "ep_voice.wav"
    timeline_starts: List[float] = []
    scene_durations: List[float] = []
    segments: List[Segment] = []

    if ep.full_audio_path is not None and ep.full_audio_path.is_file():
        audio_source = "full_audio"
        raw_voice_wav = ep_work / "raw_voice_1x.wav"
        to_wav(ep.full_audio_path, raw_voice_wav, SR, 1)
        raw_total = probe(raw_voice_wav).duration

        all_scenes = list(sb.scenes)
        _populate_full_audio_alignments(raw_voice_wav, all_scenes, raw_total, ep, config, rep)
        trimmed_raw_wav, kept_scenes, trimmed_dur = _cut_and_reassemble_full_audio(
            raw_voice_wav, all_scenes, skip_set, raw_total, ep_work
        )

        if apply_speed:
            change_speed(trimmed_raw_wav, voice_wav, speed_factor)
            ep_duration = probe(voice_wav).duration
            eff_scale = (trimmed_dur / ep_duration) if (trimmed_dur > 0 and ep_duration > 0) else speed_factor
            _scale_scenes_timings(kept_scenes, eff_scale, ep_duration)
        else:
            shutil.copyfile(str(trimmed_raw_wav), str(voice_wav))
            ep_duration = trimmed_dur

        n_kept = len(kept_scenes)
        for i, sc in enumerate(kept_scenes):
            st = 0.0 if i == 0 else float(sc.start_time)
            timeline_starts.append(st)
            if i < n_kept - 1:
                nxt_st = float(kept_scenes[i + 1].start_time)
                dur = max(0.3, round(nxt_st - st, 3)) if nxt_st > st else max(0.3, sc.duration_sec or 1.0)
            else:
                rem = round(ep_duration - st, 3)
                dur = max(0.3, rem) if rem > 0 else max(0.3, sc.duration_sec or 1.0)
            scene_durations.append(dur)

            s_words = list(sc.words or [])
            if not s_words:
                est = estimate_word_timings(sc.speech, max(0.1, dur))
                s_words = [w.scale_and_shift(speed=1.0, offset=st) for w in est]
            segments.append(Segment(start=st, end=st + dur, text=sc.speech, words=s_words))
    else:
        audio_source = "tts_per_scene"
        kept_scenes = [sc for sc in sb.scenes if sc.id not in skip_set]
        if not kept_scenes:
            raise DubberError(f"Tập '{ep.episode_id}' không còn cảnh nào sau khi cắt CTA.")

        eff_settings = settings or DubSettings()
        eff_keys = keys or Keys.from_env()
        engine = tts_engine or make_engine(
            eff_settings,
            eff_keys,
            on_fallback=lambda name, e: rep.log(f"⚠️ {name} lỗi ({e}) → chuyển engine dự phòng", "warn"),
        )
        eff_cache = cache or AudioCache()
        voice_id = getattr(engine, "voice_id", getattr(engine, "voice", ""))
        engine_params = getattr(engine, "params", {})

        engine_already_scaled = (
            getattr(engine, "name", "") == "edge"
            and str(getattr(engine, "rate", "+0%")).strip() not in ("", "+0%", "0%", "-0%")
        )
        apply_per_scene_speed = apply_speed and not engine_already_scaled

        clips_dir = ep_work / "tts_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        scene_wavs: List[Path] = []
        cursor = 0.0

        for sc in kept_scenes:
            rep.check_cancel()
            raw_audio = clips_dir / f"raw_{sc.id:03d}{engine.ext}"
            wav_audio = clips_dir / f"scene_{sc.id:03d}.wav"
            key = compute_cache_key(engine.name, voice_id, sc.speech, engine_params)
            cached = eff_cache.get(key, engine.ext)

            if cached is not None:
                cached_audio, words = cached
                shutil.copyfile(str(cached_audio), str(raw_audio))
            else:
                words = engine.synthesize(sc.speech, raw_audio) or []
                eff_cache.put(key, raw_audio, words, engine.ext)
                if getattr(engine, "name", "") == "edge":
                    time.sleep(0.35)

            to_wav(raw_audio, wav_audio, SR, 1)
            audio_dur = probe(wav_audio).duration

            if getattr(engine, "name", "") == "edge" and len(words) != len(sc.speech.split()):
                words = estimate_word_timings(sc.speech, max(0.1, audio_dur))

            if apply_per_scene_speed:
                wav_sped = clips_dir / f"scene_{sc.id:03d}_spd.wav"
                change_speed(wav_audio, wav_sped, speed_factor)
                sped_dur = probe(wav_sped).duration
                eff_scale = (audio_dur / sped_dur) if (audio_dur > 0 and sped_dur > 0) else speed_factor
                wav_audio = wav_sped
                audio_dur = sped_dur
                words = [w.scale_and_shift(speed=eff_scale, offset=0.0) for w in words]

            eff_pad = (config.scene_pad / speed_factor) if apply_per_scene_speed else config.scene_pad
            base_sc_dur = (
                (sc.duration_sec / speed_factor)
                if (apply_per_scene_speed and sc.duration_sec > 0)
                else sc.duration_sec
            )
            scene_dur = base_sc_dur if base_sc_dur > 0 else (audio_dur + eff_pad)

            sc.start_time = round(cursor, 3)
            sc.end_time = round(cursor + scene_dur, 3)
            sc.duration_sec = round(scene_dur, 3)
            shifted_words = [w.scale_and_shift(speed=1.0, offset=cursor) for w in words]
            sc.words = shifted_words

            timeline_starts.append(cursor)
            scene_durations.append(scene_dur)
            scene_wavs.append(wav_audio)
            segments.append(
                Segment(start=cursor, end=cursor + audio_dur, text=sc.speech, words=shifted_words)
            )
            cursor += scene_dur

        ep_duration = cursor
        total_samples = max(1, int(round(ep_duration * SR)))
        voice_buf = np.zeros(total_samples, dtype=np.float32)
        for start_s, w_path in zip(timeline_starts, scene_wavs):
            x = fade_edges(read_wav(w_path))
            idx_s = int(round(start_s * SR))
            if idx_s >= total_samples:
                break
            idx_e = min(idx_s + x.size, total_samples)
            voice_buf[idx_s:idx_e] = x[: idx_e - idx_s]
        write_wav(voice_wav, voice_buf)

    # Tạo track SFX và trộn ra WAV 48kHz stereo khớp chính xác ep_duration
    total_samples = max(1, int(round(ep_duration * SR)))
    eff_sfx_vol = max(0.0, min(1.5, float(config.sfx_volume)))
    sfx_wav: Optional[Path] = None
    if eff_sfx_vol > 1e-4:
        assets_dir = ep.assets_dir or ep.folder
        sfx_dirs = [d for d in (assets_dir / "sfx", ROOT_DIR / "assets" / "sfx", Path("assets/sfx")) if d is not None]
        sfx_buf = np.zeros(total_samples, dtype=np.float32)
        has_sfx = False
        for i, sc in enumerate(kept_scenes):
            if sc.sfx and sc.sfx.lower() != "none":
                try:
                    sfx_data, _ = load_or_synthesize_sfx(sc.sfx, sfx_dirs, ep_work, sample_rate=SR, index=i)
                    if sfx_data is None or sfx_data.size == 0:
                        continue
                    idx_s = max(0, int(round(timeline_starts[i] * SR)))
                    if idx_s >= total_samples:
                        continue
                    idx_e = min(idx_s + sfx_data.size, total_samples)
                    if idx_e > idx_s:
                        seg_sfx = sfx_data[: idx_e - idx_s].copy()
                        if idx_e - idx_s < sfx_data.size:
                            n_tail = min(int(round(SR * 0.02)), seg_sfx.size // 2)
                            if n_tail > 0:
                                seg_sfx[-n_tail:] *= np.linspace(1.0, 0.0, n_tail, dtype=np.float32)
                        sfx_buf[idx_s:idx_e] += seg_sfx
                        has_sfx = True
                except Exception:
                    pass
        if has_sfx:
            peak = float(np.max(np.abs(sfx_buf)))
            if peak > 0.92:
                sfx_buf = (np.tanh(sfx_buf * (1.25 / peak)) / math.tanh(1.25)).astype(np.float32) * 0.92
            sfx_wav = ep_work / "ep_sfx.wav"
            write_wav(sfx_wav, sfx_buf)

    ep_audio_48k = ep_work / "ep_audio_48k.wav"
    if sfx_wav is not None:
        fc = (
            f"[0:a]aresample={YT_AUDIO_SR},aformat=sample_fmts=fltp:sample_rates={YT_AUDIO_SR}:channel_layouts=stereo[v];"
            f"[1:a]aresample={YT_AUDIO_SR},aformat=sample_fmts=fltp:sample_rates={YT_AUDIO_SR}:channel_layouts=stereo,"
            f"volume={eff_sfx_vol:.3f}[s];"
            f"[v][s]amix=inputs=2:duration=first:normalize=0"
        )
        res = run_ffmpeg(
            ["-i", str(voice_wav), "-i", str(sfx_wav), "-filter_complex", fc, "-t", f"{ep_duration:.4f}", "-c:a", "pcm_s16le", str(ep_audio_48k)],
            check=False,
        )
        if res.returncode != 0:
            fc_fb = fc.replace(":normalize=0", "")
            run_ffmpeg(["-i", str(voice_wav), "-i", str(sfx_wav), "-filter_complex", fc_fb, "-t", f"{ep_duration:.4f}", "-c:a", "pcm_s16le", str(ep_audio_48k)])
    else:
        run_ffmpeg([
            "-i", str(voice_wav),
            "-t", f"{ep_duration:.4f}",
            "-ac", "2", "-ar", str(YT_AUDIO_SR), "-c:a", "pcm_s16le",
            str(ep_audio_48k),
        ])

    return kept_scenes, timeline_starts, scene_durations, segments, ep_audio_48k, ep_duration, audio_source


# ---------------------------------------------------------------------------
# 4. Vẽ Thẻ Chương & End Credits bằng Pillow (KHÔNG dùng drawtext)
# ---------------------------------------------------------------------------
def _load_vn_font(size: int, *, bold: bool = False) -> Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]:
    """Tải font hỗ trợ đầy đủ tiếng Việt UTF-8 trên Windows/Linux, fallback an toàn."""
    candidates = _CARD_FONT_CANDIDATES_BOLD if bold else _CARD_FONT_CANDIDATES_REGULAR
    for path_str in candidates:
        if os.path.isfile(path_str):
            try:
                return ImageFont.truetype(path_str, size=size)
            except Exception:
                continue
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text_pil(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Union[ImageFont.FreeTypeFont, ImageFont.ImageFont],
    max_width: int,
    max_lines: int = 3,
) -> List[str]:
    """Ngắt dòng tự động dựa trên bề rộng pixel thực tế."""
    words = (text or "").split()
    if not words:
        return []
    lines: List[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) >= max_lines - 1:
                break
    consumed_words = sum(len(ln.split()) for ln in lines)
    rem_words = words[consumed_words:]
    if rem_words:
        tail = " ".join(rem_words)
        while len(tail) > 4:
            bbox = draw.textbbox((0, 0), tail, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                break
            tail = tail[:-4].rstrip() + "..."
        lines.append(tail)
    elif cur:
        lines.append(cur)
    return lines[:max_lines]


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: Union[ImageFont.FreeTypeFont, ImageFont.ImageFont],
    fill: Tuple[int, int, int],
    width: int = YT_WIDTH,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = max(20, (width - tw) // 2)
    draw.text((x, y), text, font=font, fill=fill)
    return y + th


def render_chapter_card_image(
    chapter_index: int,
    chapter_title: str,
    out_path: Union[Path, str],
    *,
    series_title: str = "",
    subtitle: str = "",
    width: int = YT_WIDTH,
    height: int = YT_HEIGHT,
) -> Path:
    """Vẽ ảnh Thẻ tiêu đề chương (Chapter Card) 1920x1080 phong cách phim tài liệu bằng Pillow."""
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Nền than chì tối giản có chiều sâu nhẹ ở tâm
    img = Image.new("RGB", (width, height), (11, 13, 18))
    draw = ImageDraw.Draw(img)
    draw.rectangle([48, 48, width - 48, height - 48], outline=(42, 46, 56), width=2)

    font_series = _load_vn_font(28, bold=False)
    font_label = _load_vn_font(42, bold=True)
    font_title = _load_vn_font(68, bold=True)
    font_sub = _load_vn_font(30, bold=False)

    gold = (212, 175, 55)
    ivory = (244, 241, 234)
    muted = (165, 170, 180)

    part_label = f"PHẦN {int(chapter_index):02d}"
    title_lines = _wrap_text_pil(draw, chapter_title or f"Tập {int(chapter_index):02d}", font_title, int(width * 0.76), max_lines=3)

    # Tính tổng chiều cao khối nội dung để căn giữa chuẩn dọc
    line_heights = [draw.textbbox((0, 0), ln, font=font_title)[3] - draw.textbbox((0, 0), ln, font=font_title)[1] for ln in title_lines]
    block_h = 56 + 32 + sum(line_heights) + max(0, len(line_heights) - 1) * 20
    if series_title:
        block_h += 54
    if subtitle:
        block_h += 54

    y = max(110, (height - block_h) // 2)

    if series_title:
        y = _draw_centered_text(draw, series_title.upper(), y, font_series, muted, width) + 24

    y = _draw_centered_text(draw, part_label, y, font_label, gold, width) + 22

    rule_half = 140
    cx = width // 2
    draw.line([(cx - rule_half, y), (cx + rule_half, y)], fill=gold, width=3)
    y += 32

    for ln in title_lines:
        y = _draw_centered_text(draw, ln, y, font_title, ivory, width) + 18

    if subtitle:
        y += 14
        _draw_centered_text(draw, subtitle, y, font_sub, muted, width)

    img.save(str(out_p), format="PNG")
    return out_p


def render_end_credits_image(
    license_report: LicenseAuditReport,
    out_path: Union[Path, str],
    *,
    series_title: str = "",
    channel_name: str = "",
    max_items: int = 10,
    width: int = YT_WIDTH,
    height: int = YT_HEIGHT,
) -> Path:
    """Vẽ ảnh End Credits 1920x1080 liệt kê nguồn tư liệu CC BY / CC BY-SA & Public Domain."""
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), (10, 12, 16))
    draw = ImageDraw.Draw(img)
    draw.rectangle([52, 52, width - 52, height - 52], outline=(44, 48, 58), width=2)

    gold = (212, 175, 55)
    ivory = (244, 241, 234)
    silver = (205, 210, 218)
    muted = (150, 156, 168)

    font_header = _load_vn_font(46, bold=True)
    font_sub = _load_vn_font(26, bold=False)
    font_item = _load_vn_font(24, bold=False)

    y = 95
    y = _draw_centered_text(draw, "NGUỒN TƯ LIỆU & GHI CÔNG BẢN QUYỀN", y, font_header, gold, width) + 14
    if series_title:
        y = _draw_centered_text(draw, series_title, y, font_sub, ivory, width) + 16

    cx = width // 2
    draw.line([(cx - 220, y), (cx + 220, y)], fill=gold, width=2)
    y += 30

    # Ưu tiên hiển thị nguồn cần ghi công CC BY / CC BY-SA trước, rồi Public Domain
    ordered_items = [*license_report.cc_attribution_required, *license_report.public_domain]
    shown = ordered_items[:max(1, int(max_items))]
    overflow = max(0, len(ordered_items) - len(shown))

    if not shown:
        _draw_centered_text(
            draw,
            "Tư liệu tổng hợp và minh họa bởi đội ngũ biên tập.",
            y + 80,
            font_sub,
            silver,
            width,
        )
    else:
        for idx, src in enumerate(shown, start=1):
            clean_title = re.sub(r"^File:", "", src.title or "Tư liệu lưu trữ", flags=re.I).strip()
            if "." in clean_title and len(clean_title.rsplit(".", 1)[-1]) <= 4:
                clean_title = clean_title.rsplit(".", 1)[0]
            if len(clean_title) > 58:
                clean_title = clean_title[:55].rstrip() + "..."
            author_part = f" — {src.author}" if src.author else ""
            lic_part = f" [{src.license}]" if src.license else ""
            line = f"{idx:02d}. {clean_title}{author_part}{lic_part}"
            wrapped = _wrap_text_pil(draw, line, font_item, width - 220, max_lines=1)
            if wrapped:
                y = _draw_centered_text(draw, wrapped[0], y, font_item, silver, width) + 14
            if y > height - 155:
                overflow = max(overflow, len(ordered_items) - idx)
                break

    footer_y = height - 118
    if overflow > 0:
        footer_y = _draw_centered_text(
            draw,
            f"(+ {overflow} tư liệu khác — xem danh sách & liên kết đầy đủ dưới phần mô tả video)",
            footer_y,
            font_sub,
            gold,
            width,
        ) + 10
    elif ordered_items:
        footer_y = _draw_centered_text(
            draw,
            "Liên kết gốc đầy đủ được đính kèm dưới phần mô tả video.",
            footer_y,
            font_sub,
            muted,
            width,
        ) + 10

    if channel_name:
        _draw_centered_text(draw, channel_name, footer_y, font_sub, ivory, width)

    img.save(str(out_p), format="PNG")
    return out_p


def render_still_card_clip(
    image_path: Union[Path, str],
    out_clip: Union[Path, str],
    duration: float,
    *,
    fade_in: float = 0.45,
    fade_out: float = 0.45,
    fps: int = YT_FPS,
    crf: int = 19,
    preset: str = "veryfast",
) -> Path:
    """Encode ảnh PNG thẻ chương / end credits thành clip MKV chuẩn 1920x1080@30fps + audio im lặng 48kHz stereo."""
    dur = max(0.4, float(duration))
    fi = min(max(0.0, fade_in), dur * 0.35)
    fo = min(max(0.0, fade_out), dur * 0.35)
    fo_st = max(0.0, dur - fo)

    vf_parts = [f"scale={YT_WIDTH}:{YT_HEIGHT}:flags=lanczos", "setsar=1", f"fps={fps}"]
    if fi > 0.02:
        vf_parts.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo > 0.02:
        vf_parts.append(f"fade=t=out:st={fo_st:.3f}:d={fo:.3f}")
    vf_parts.append("format=yuv420p")

    out_p = Path(out_clip)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-framerate", str(fps),
        "-loop", "1",
        "-t", f"{dur:.3f}",
        "-i", str(image_path),
        "-f", "lavfi",
        "-t", f"{dur:.3f}",
        "-i", f"anullsrc=r={YT_AUDIO_SR}:cl=stereo",
        "-vf", ",".join(vf_parts),
        "-t", f"{dur:.3f}",
        "-r", str(fps),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-preset", preset,
        "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        "-ar", str(YT_AUDIO_SR),
        "-ac", "2",
        str(out_p),
    ])
    return out_p


# ---------------------------------------------------------------------------
# 5 & 6. Render phân mảnh, Concat, Mastering & Xuất trọn bộ YouTube
# ---------------------------------------------------------------------------
def format_youtube_timestamp(seconds: float) -> str:
    """Định dạng giây sang mốc YouTube Chapters (`MM:SS` hoặc `H:MM:SS`)."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_youtube_description(
    series_name: str,
    chapters: Sequence[ChapterMarker],
    license_report: LicenseAuditReport,
    *,
    intro_text: str = "",
    channel_name: str = "",
) -> str:
    """Sinh nội dung `youtube_description.txt` gồm YouTube Chapters và danh sách ghi nguồn."""
    lines: List[str] = []
    display_title = (series_name or "Series Tài Liệu").replace("_", " ").strip()
    lines.append(display_title)
    if intro_text:
        lines.append("")
        lines.append(intro_text.strip())

    lines.append("")
    lines.append("MỤC LỤC (TIMESTAMPS / CHAPTERS):")
    for idx, ch in enumerate(chapters, start=1):
        ts = "00:00" if idx == 1 else format_youtube_timestamp(ch.start_sec)
        lines.append(f"{ts} Phần {ch.chapter_index}: {ch.chapter_title}")

    if license_report.cc_attribution_required:
        lines.append("")
        lines.append("NGUỒN TƯ LIỆU YÊU CẦU GHI CÔNG (CC BY / CC BY-SA):")
        for item in license_report.cc_attribution_required:
            lines.append(f"- {item.credit_line()}")

    if license_report.public_domain:
        lines.append("")
        lines.append("NGUỒN TƯ LIỆU PHẠM VI CÔNG CỘNG (PUBLIC DOMAIN):")
        for item in license_report.public_domain:
            lines.append(f"- {item.credit_line()}")

    if license_report.other_sources:
        lines.append("")
        lines.append("NGUỒN THAM KHẢO KHÁC:")
        for item in license_report.other_sources:
            lines.append(f"- {item.credit_line()}")

    if channel_name:
        lines.append("")
        lines.append(f"— {channel_name} —")

    return "\n".join(lines).rstrip() + "\n"


def _safe_filename_stem(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "Series").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned or "Series"


def _render_scene_video_16x9(
    asset_file: Path,
    duration: float,
    out_clip: Path,
    *,
    motion: str,
    layout_mode: str,
    is_video: bool,
    is_archival: bool,
    anti_ai_level: Union[str, bool, None],
    crf: int,
    preset: str,
) -> Path:
    """Render một cảnh (ảnh tĩnh hoặc video clip) ra clip MKV chuẩn 1920x1080 @ 30fps (không audio)."""
    dur = max(0.15, float(duration))
    src_size = probe_media_dimensions(asset_file)
    vf = build_youtube_16x9_scene_vf(
        asset_file,
        dur,
        motion=motion,
        layout_mode=layout_mode,
        is_video=is_video,
        is_archival=is_archival,
        anti_ai_level=anti_ai_level,
        src_size=src_size,
        fps=YT_FPS,
        out_w=YT_WIDTH,
        out_h=YT_HEIGHT,
    )

    out_clip.parent.mkdir(parents=True, exist_ok=True)
    if is_video:
        stretch_prefix = ""
        try:
            src_dur = probe(asset_file).duration
            if src_dur > 0.2 and src_dur < dur and (dur / src_dur) <= 1.45:
                stretch_prefix = f"setpts={(dur / src_dur):.5f}*PTS,"
        except Exception:
            pass

        if stretch_prefix:
            cmd = [
                "-i", str(asset_file),
                "-t", f"{dur:.3f}",
                "-vf", f"{stretch_prefix}{vf}",
            ]
        else:
            cmd = [
                "-stream_loop", "-1",
                "-i", str(asset_file),
                "-t", f"{dur:.3f}",
                "-vf", vf,
            ]
    else:
        cmd = [
            "-framerate", "1",
            "-loop", "1",
            "-i", str(asset_file),
            "-vf", vf,
            "-t", f"{dur:.3f}",
        ]

    cmd += [
        "-r", str(YT_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-preset", preset,
        "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_clip),
    ]
    res = run_ffmpeg(cmd, check=False)
    if res.returncode != 0:
        raise DubberError(f"Render cảnh 16:9 '{asset_file.name}' thất bại:\n{res.stderr.strip()[-800:]}")
    return out_clip


def _render_episode_chunk(
    ep: EpisodeEntry,
    chapter_index: int,
    config: SeriesCompileConfig,
    work_dir: Path,
    rep: Reporter,
    *,
    tts_engine: Optional[TTSEngine] = None,
    cache: Optional[AudioCache] = None,
    settings: Optional[DubSettings] = None,
    keys: Optional[Keys] = None,
) -> Tuple[Path, float, ChapterMarker, List[Segment]]:
    """Render toàn bộ một tập (tùy chọn Thẻ chương + tập MKV chuẩn 1920x1080/48kHz) và trả mốc thực tế."""
    ep_work = work_dir / f"ep_{chapter_index:02d}_{_safe_filename_stem(ep.episode_id)}"
    ep_work.mkdir(parents=True, exist_ok=True)

    sb = load_storyboard(ep.folder / "storyboard.json")
    sb.ensure_scenes_from_beats()
    assets_dir = ep.assets_dir or (ep.folder / "assets" if (ep.folder / "assets").is_dir() else ep.folder)
    assets_16x9_dir = ep.assets_16x9_dir or ((ep.folder / "assets_16x9") if (ep.folder / "assets_16x9").is_dir() else None)
    asset_map, missing, native_16x9_all, fallback_all = _resolve_episode_scene_assets(
        sb,
        assets_dir,
        assets_16x9_dir,
        prefer_assets_16x9=bool(config.prefer_assets_16x9),
    )
    if missing:
        raise DubberError(f"Tập '{ep.episode_id}' thiếu tài nguyên cho các cảnh: {missing}")

    archival_ids = (
        load_archival_scene_ids(ep.folder)
        | load_archival_scene_ids(assets_dir)
        | (load_archival_scene_ids(assets_16x9_dir) if assets_16x9_dir else set())
    )
    skip_list = config.episode_skip_overrides.get(ep.episode_id)
    if skip_list is None:
        skip_list = ep.resolve_skip_scenes(auto_skip_mid_cta=config.auto_skip_mid_cta)
    skip_set = {int(s) for s in skip_list}

    kept_scenes, timeline_starts, scene_durations, ep_segments, ep_audio_48k, ep_duration, audio_source = (
        _prepare_episode_audio_and_timeline(
            ep,
            sb,
            skip_set,
            float(config.speed_factor),
            ep_work,
            config,
            rep,
            tts_engine=tts_engine,
            cache=cache,
            settings=settings,
            keys=keys,
        )
    )

    scene_clips: List[Path] = []
    scenes_dir = ep_work / "scenes_16x9"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    n_kept = len(kept_scenes)
    native_16x9_set = set(native_16x9_all)
    fallback_set = set(fallback_all)
    native_16x9_kept = [sc.id for sc in kept_scenes if sc.id in native_16x9_set]
    fallback_kept = [sc.id for sc in kept_scenes if sc.id in fallback_set]

    for i, sc in enumerate(kept_scenes):
        rep.check_cancel()
        asset_file = asset_map[sc.id]
        is_video = asset_file.suffix.lower() in ASSET_VIDEO_EXTS
        # Cảnh cuối bù nhẹ 0.05s hình để mux -t ep_duration không hụt frame
        render_dur = scene_durations[i] + (0.05 if i == n_kept - 1 else 0.0)
        clip_path = scenes_dir / f"sc_{sc.id:03d}.mkv"
        _render_scene_video_16x9(
            asset_file,
            render_dur,
            clip_path,
            motion=sc.ken_burns or sc.motion,
            layout_mode=config.layout_mode,
            is_video=is_video,
            is_archival=(sc.id in archival_ids),
            anti_ai_level=config.anti_ai_level,
            crf=config.crf,
            preset=config.preset,
        )
        scene_clips.append(clip_path)

    ep_video_only = ep_work / "ep_video_only.mkv"
    if len(scene_clips) == 1:
        ep_video_only = scene_clips[0]
    else:
        concat_txt = ep_work / "scenes_concat.txt"
        concat_txt.write_text(
            "".join(f"file '{p.relative_to(ep_work).as_posix()}'\n" for p in scene_clips),
            encoding="utf-8",
        )
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_txt.name, "-c", "copy", str(ep_video_only)], cwd=ep_work)

    ep_muxed_mkv = work_dir / f"chunk_ep_{chapter_index:02d}.mkv"
    run_ffmpeg([
        "-i", str(ep_video_only),
        "-i", str(ep_audio_48k),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "pcm_s16le",
        "-ar", str(YT_AUDIO_SR),
        "-ac", "2",
        "-t", f"{ep_duration:.4f}",
        str(ep_muxed_mkv),
    ])

    ep_actual_dur = probe(ep_muxed_mkv).duration
    skipped_actual = sorted(sc.id for sc in sb.scenes if sc.id in skip_set)
    archival_kept = sorted(sc.id for sc in kept_scenes if sc.id in archival_ids)
    marker = ChapterMarker(
        chapter_index=chapter_index,
        episode_id=ep.episode_id,
        chapter_title=ep.chapter_title,
        start_sec=0.0,
        episode_start_sec=0.0,
        duration_sec=ep_actual_dur,
        card_duration_sec=0.0,
        kept_scenes=[sc.id for sc in kept_scenes],
        skipped_scenes=skipped_actual,
        archival_scenes=archival_kept,
        audio_source=audio_source,
        native_16x9_scenes=native_16x9_kept,
        fallback_assets_scenes=fallback_kept,
    )
    return ep_muxed_mkv, ep_actual_dur, marker, ep_segments


def _shift_segment(seg: Segment, offset_sec: float) -> Segment:
    shifted_words = [
        WordTiming(
            word=w.word,
            start=round(w.start + offset_sec, 4),
            end=round(w.end + offset_sec, 4),
        )
        for w in (seg.words or [])
    ]
    return Segment(
        start=round(seg.start + offset_sec, 3),
        end=round(seg.end + offset_sec, 3),
        text=seg.text,
        src=seg.src,
        words=shifted_words,
    )


def _master_series_mp4(
    concat_mkv: Path,
    out_mp4: Path,
    master_duration: float,
    all_segments: Sequence[Segment],
    config: SeriesCompileConfig,
    work_dir: Path,
    rep: Reporter,
) -> None:
    """Trộn BGM 1.00x (tùy chọn), đốt phụ đề (nếu chọn burn_and_srt), chuẩn hóa -14 LUFS & xóa metadata."""
    resolved_bgm: Optional[Path] = None
    if config.bgm_path:
        cand = Path(config.bgm_path)
        if cand.is_file():
            resolved_bgm = cand.resolve()

    prepared_bgm: Optional[Path] = None
    if resolved_bgm is not None:
        prepared_bgm = _prepare_bgm_audio(resolved_bgm, work_dir, master_duration, reporter=rep)

    burn_subs = config.subtitle_mode == "burn_and_srt"
    has_wm = bool((config.watermark_text or "").strip())
    need_ass_burn = burn_subs or has_wm
    ass_name = "yt_subs.ass"
    if need_ass_burn:
        generate_story_karaoke_ass(
            all_segments,
            work_dir / ass_name,
            font_size=config.sub_font_size,
            font_name=config.sub_font,
            subtitle_style=config.subtitle_style,
            sub_color=config.sub_color,
            watermark_text=(config.watermark_text or "").strip() or None,
            watermark_pos=config.watermark_pos,
            watermark_opacity=config.watermark_opacity,
            total_duration=master_duration,
            burn_subtitles=burn_subs,
            frame_preset="landscape_16_9",
        )

    post_audio: List[str] = []
    if config.loudnorm_youtube:
        post_audio.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    post_audio.append(f"aresample={YT_AUDIO_SR}")
    post_audio.append(f"aformat=sample_fmts=fltp:sample_rates={YT_AUDIO_SR}:channel_layouts=stereo")
    post_audio.append("alimiter=limit=0.95:attack=5:release=50:level=0")
    post_chain = ",".join(post_audio)

    bgm_fade_in = min(1.0, max(0.1, master_duration * 0.15))
    bgm_fade_out = min(1.8, max(0.2, master_duration * 0.25))
    bgm_fade_out_st = max(0.0, master_duration - bgm_fade_out)

    def build_cmd(*, include_normalize: bool, force_reencode_video: bool) -> List[str]:
        inputs: List[str] = ["-i", str(concat_mkv)]
        if prepared_bgm is not None:
            inputs += ["-i", str(prepared_bgm)]

        fc_parts: List[str] = []
        v_map = "0:v:0"
        if need_ass_burn:
            fc_parts.append(f"[0:v]ass={ass_name},format=yuv420p[vout]")
            v_map = "[vout]"

        norm_opt = ":normalize=0" if include_normalize else ""
        fmt_48k = f"aresample={YT_AUDIO_SR},aformat=sample_fmts=fltp:sample_rates={YT_AUDIO_SR}:channel_layouts=stereo"

        if prepared_bgm is not None:
            bgm_vol = max(0.0, min(1.0, float(config.bgm_volume)))
            fc_parts.append(
                f"[0:a]{fmt_48k},asplit=2[voice_sc][voice_mix];"
                f"[1:a]{fmt_48k},volume={bgm_vol:.3f},"
                f"atrim=0:{master_duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={bgm_fade_in:.3f},"
                f"afade=t=out:st={bgm_fade_out_st:.3f}:d={bgm_fade_out:.3f}[bg];"
                f"[bg][voice_sc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400[ducked_bg];"
                f"[voice_mix][ducked_bg]amix=inputs=2:duration=first{norm_opt},{post_chain}[aout]"
            )
        else:
            fc_parts.append(f"[0:a]{fmt_48k},{post_chain}[aout]")

        if need_ass_burn or force_reencode_video:
            v_args = [
                "-r", str(YT_FPS),
                "-c:v", "libx264",
                "-profile:v", "high",
                "-preset", config.preset,
                "-crf", str(int(config.crf)),
                "-pix_fmt", "yuv420p",
            ]
        else:
            v_args = ["-c:v", "copy"]

        return [
            *inputs,
            "-filter_complex", ";".join(fc_parts),
            "-map", v_map,
            "-map", "[aout]",
            "-t", f"{master_duration:.4f}",
            *v_args,
            "-c:a", "aac",
            "-ar", str(YT_AUDIO_SR),
            "-ac", "2",
            "-b:a", "192k",
            *get_clean_metadata_flags(),
            str(out_mp4),
        ]

    res = run_ffmpeg(build_cmd(include_normalize=True, force_reencode_video=False), cwd=work_dir, check=False)
    if res.returncode != 0 and "normalize" in res.stderr:
        res = run_ffmpeg(build_cmd(include_normalize=False, force_reencode_video=False), cwd=work_dir, check=False)
    if res.returncode != 0 and not need_ass_burn:
        res = run_ffmpeg(build_cmd(include_normalize=False, force_reencode_video=True), cwd=work_dir, check=False)
    if res.returncode != 0:
        raise DubberError(f"Mastering series YouTube thất bại:\n{res.stderr.strip()[-900:]}")


def compile_series_youtube(
    series_dir: Union[Path, str],
    config: Optional[SeriesCompileConfig] = None,
    *,
    episodes: Optional[Sequence[EpisodeEntry]] = None,
    reporter: Optional[Reporter] = None,
    tts_engine: Optional[TTSEngine] = None,
    cache: Optional[AudioCache] = None,
    settings: Optional[DubSettings] = None,
    keys: Optional[Keys] = None,
) -> SeriesCompileResult:
    """Biên tập trọn bộ nhiều tập thành 1 video YouTube 16:9 + 4 file đầu ra chuẩn xuất bản."""
    cfg = config or SeriesCompileConfig()
    rep = reporter or Reporter()

    if cfg.layout_mode not in VALID_LAYOUT_MODES:
        raise DubberError(f"layout_mode '{cfg.layout_mode}' không hợp lệ (chấp nhận: {VALID_LAYOUT_MODES}).")
    if cfg.subtitle_mode not in VALID_SUBTITLE_MODES:
        raise DubberError(f"subtitle_mode '{cfg.subtitle_mode}' không hợp lệ (chấp nhận: {VALID_SUBTITLE_MODES}).")
    spd = float(cfg.speed_factor)
    if not (MIN_SPEED_FACTOR <= spd <= MAX_SPEED_FACTOR):
        raise DubberError(
            f"speed_factor={spd} không hợp lệ (chỉ chấp nhận {MIN_SPEED_FACTOR:.2f}–{MAX_SPEED_FACTOR:.2f})."
        )

    series_root = Path(series_dir).resolve()
    scanned = list(episodes) if episodes is not None else scan_series_folder(series_root)
    active_episodes = [ep for ep in scanned if ep.enabled]
    if not active_episodes:
        raise DubberError("Không có tập nào được chọn để biên tập.")

    missing_eps = [f"{ep.episode_id}:{ep.missing_assets}" for ep in active_episodes if ep.missing_assets]
    if missing_eps:
        raise DubberError(f"Thiếu tài nguyên cảnh trong series: {', '.join(missing_eps)}")

    series_name = cfg.series_name or series_root.name
    safe_stem = _safe_filename_stem(series_name)
    display_series_title = cfg.series_title or series_name.replace("_", " ").strip()

    out_dir = Path(cfg.output_dir).resolve() if cfg.output_dir else (series_root / "youtube_export")
    out_dir.mkdir(parents=True, exist_ok=True)

    video_out = out_dir / f"{safe_stem}_Full_16x9.mp4"
    srt_out = out_dir / f"{safe_stem}_subtitles.srt"
    desc_out = out_dir / "youtube_description.txt"
    report_out = out_dir / "compliance_report.json"

    rep.progress(3, "Đang kiểm tra bản quyền tư liệu toàn series...")
    license_report = audit_series_licenses(active_episodes)
    if license_report.has_risky_warnings:
        rep.log(
            f"⚠️ Phát hiện {len(license_report.risky_warnings)} nguồn có cờ rủi ro bản quyền — kiểm tra compliance_report.json!",
            "warn",
        )
    rep.check_cancel()

    work_base = Path(cfg.work_root) if cfg.work_root else WORK_ROOT
    work_dir = work_base / time.strftime(f"series_{safe_stem}_%Y%m%d_%H%M%S")
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        concat_clips: List[Path] = []
        chapters: List[ChapterMarker] = []
        all_srt_segments: List[Segment] = []
        cursor = 0.0
        total_eps = len(active_episodes)
        rep.log(
            f"📺 Bắt đầu biên tập Series 16:9 '{display_series_title}': {total_eps} tập | tốc độ {spd:.2f}x | bố cục {cfg.layout_mode}.",
            "accent",
        )

        for idx, ep in enumerate(active_episodes, start=1):
            rep.check_cancel()
            rep.progress(
                8 + int((idx - 1) * 68 / max(1, total_eps)),
                f"Đang render Phần {idx}/{total_eps}: {ep.chapter_title}",
            )
            rep.log(f"▶️ Phần {idx}/{total_eps}: {ep.episode_id} — '{ep.chapter_title}'", "info")

            card_dur = 0.0
            if cfg.include_chapter_cards and cfg.chapter_card_duration > 0.2:
                card_img = work_dir / f"card_{idx:02d}.png"
                render_chapter_card_image(
                    idx,
                    ep.chapter_title,
                    card_img,
                    series_title=display_series_title,
                )
                card_mkv = work_dir / f"chunk_card_{idx:02d}.mkv"
                render_still_card_clip(
                    card_img,
                    card_mkv,
                    cfg.chapter_card_duration,
                    crf=cfg.crf,
                    preset=cfg.preset,
                )
                card_dur = probe(card_mkv).duration
                concat_clips.append(card_mkv)

            rep.check_cancel()
            ep_mkv, ep_actual_dur, marker, ep_segments = _render_episode_chunk(
                ep,
                idx,
                cfg,
                work_dir,
                rep,
                tts_engine=tts_engine,
                cache=cache,
                settings=settings,
                keys=keys,
            )
            concat_clips.append(ep_mkv)
            skip_note = f" | cắt CTA {marker.skipped_scenes}" if marker.skipped_scenes else ""
            src_note = (
                f" | 16:9 gốc {len(marker.native_16x9_scenes)}/{len(marker.kept_scenes)}"
                if cfg.prefer_assets_16x9
                else ""
            )
            rep.log(f"✓ Xong Phần {idx}/{total_eps} ({ep_actual_dur:.1f}s{skip_note}{src_note}).", "ok")

            chapter_start = cursor
            episode_start = cursor + card_dur
            marker.start_sec = round(chapter_start, 3)
            marker.episode_start_sec = round(episode_start, 3)
            marker.card_duration_sec = round(card_dur, 3)
            chapters.append(marker)

            for seg in ep_segments:
                all_srt_segments.append(_shift_segment(seg, episode_start))

            cursor += card_dur + ep_actual_dur

        rep.check_cancel()
        if cfg.include_end_credits and cfg.end_credits_duration > 0.2:
            rep.progress(78, "Đang tạo thẻ End Credits ghi nguồn tư liệu...")
            rep.log("🎞️ Đang tạo thẻ End Credits ghi nguồn tư liệu...", "info")
            credits_img = work_dir / "end_credits.png"
            render_end_credits_image(
                license_report,
                credits_img,
                series_title=display_series_title,
                channel_name=cfg.channel_name,
            )
            credits_mkv = work_dir / "chunk_end_credits.mkv"
            render_still_card_clip(
                credits_img,
                credits_mkv,
                cfg.end_credits_duration,
                crf=cfg.crf,
                preset=cfg.preset,
            )
            concat_clips.append(credits_mkv)

        rep.check_cancel()
        rep.progress(82, "Đang ghép nối chuỗi tập & thẻ chương (FFmpeg concat)...")
        rep.log(f"🔗 Đang ghép nối {len(concat_clips)} phân đoạn (FFmpeg concat)...", "info")
        concat_list = work_dir / "series_concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.name}'\n" for p in concat_clips),
            encoding="utf-8",
        )
        master_mkv = work_dir / "series_master_concat.mkv"
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_list.name, "-c", "copy", str(master_mkv)], cwd=work_dir)
        master_duration = probe(master_mkv).duration

        rep.check_cancel()
        rep.progress(90, "Đang trộn BGM 1.00x, chuẩn hóa -14 LUFS và xuất MP4 sạch metadata...")
        lufs_note = "loudnorm -14 LUFS" if cfg.loudnorm_youtube else "không loudnorm"
        rep.log(f"🎚️ Đang mastering ({lufs_note}, phụ đề {cfg.subtitle_mode})...", "info")
        _master_series_mp4(master_mkv, video_out, master_duration, all_srt_segments, cfg, work_dir, rep)

        out_details = probe_video_details(video_out)

        # Xuất file phụ đề .srt chuẩn khớp 100% thời lượng thực tế
        srt_text = format_srt(all_srt_segments)
        srt_out.write_text(srt_text, encoding="utf-8")

        # Xuất mô tả YouTube Chapters + Nguồn bản quyền
        desc_text = build_youtube_description(
            display_series_title,
            chapters,
            license_report,
            intro_text=cfg.intro_description,
            channel_name=cfg.channel_name,
        )
        desc_out.write_text(desc_text, encoding="utf-8")

        # Xuất báo cáo tuân thủ compliance_report.json
        report_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "series_name": series_name,
            "series_title": display_series_title,
            "series_dir": str(series_root),
            "output_dir": str(out_dir),
            "outputs": {
                "video": str(video_out),
                "subtitles_srt": str(srt_out),
                "youtube_description": str(desc_out),
                "compliance_report": str(report_out),
            },
            "settings": {
                "layout_mode": cfg.layout_mode,
                "subtitle_mode": cfg.subtitle_mode,
                "speed_factor": spd,
                "anti_ai_level": normalize_anti_ai_level(cfg.anti_ai_level),
                "loudnorm_youtube": bool(cfg.loudnorm_youtube),
                "target_lufs": -14.0 if cfg.loudnorm_youtube else None,
                "include_chapter_cards": bool(cfg.include_chapter_cards),
                "chapter_card_duration": cfg.chapter_card_duration,
                "include_end_credits": bool(cfg.include_end_credits),
                "end_credits_duration": cfg.end_credits_duration,
                "auto_skip_mid_cta": bool(cfg.auto_skip_mid_cta),
                "prefer_assets_16x9": bool(cfg.prefer_assets_16x9),
                "has_bgm": bool(cfg.bgm_path and Path(cfg.bgm_path).is_file()),
            },
            "assets_16x9_summary": {
                "native_16x9_count": sum(len(c.native_16x9_scenes) for c in chapters),
                "fallback_assets_count": sum(len(c.fallback_assets_scenes) for c in chapters),
            },
            "video_specs": {
                "width": out_details.width,
                "height": out_details.height,
                "fps": out_details.fps,
                "duration_sec": round(out_details.duration, 3),
                "has_video": out_details.has_video,
                "has_audio": out_details.has_audio,
                "metadata_stripped": True,
            },
            "episodes": [c.to_dict() for c in chapters],
            "license_audit": license_report.to_dict(),
        }
        report_out.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        rep.progress(100, f"Hoàn tất xuất Series 16:9 ({out_details.duration:.1f}s)")
        rep.log(f"🎬 Hoàn thành Series 16:9: {video_out} ({out_details.width}x{out_details.height} @ {out_details.fps:.0f}fps)", "ok")

        return SeriesCompileResult(
            video_path=video_out,
            srt_path=srt_out,
            description_path=desc_out,
            report_path=report_out,
            chapters=chapters,
            license_report=license_report,
            total_duration_sec=out_details.duration,
            width=out_details.width,
            height=out_details.height,
            fps=out_details.fps,
            loudnorm_applied=bool(cfg.loudnorm_youtube),
        )
    finally:
        if not cfg.keep_temp:
            shutil.rmtree(work_dir, ignore_errors=True)
