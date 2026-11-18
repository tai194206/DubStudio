"""Lõi dựng phim Story Mode 9:16 cho DubStudio (Phase 3).

Quy trình:
1. Khớp tài nguyên ảnh tĩnh/clip theo id từng cảnh trong Storyboard.
2. Sinh TTS & đo thời lượng từng cảnh (có AudioCache hash), gom WordTiming cho phụ đề Karaoke 9:16.
3. Render từng cảnh 9:16 (1080x1920, 30fps) bằng hiệu ứng Ken Burns (zoompan) hoặc trim/loop clip.
4. Ghép nối video, trộn âm thanh (Voice + BGM ducking + SFX) và burn phụ đề Karaoke ASS TikTok safe area.

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pysubs2
from pysubs2 import Alignment, Color, SSAEvent, SSAFile, SSAStyle

from .audio_track import SR, fade_edges, read_wav, write_wav
from .cache import AudioCache, compute_cache_key
from .config import OUTPUT_DIR, ROOT_DIR, WORK_ROOT, DubSettings, Keys
from .elevenlabs_alignment import (
    align_scenes_with_words,
    chars_to_words,
    export_karaoke_ass,
    generate_voice_with_timestamps,
)
from .errors import Cancelled, DubberError
from .ffmpeg_utils import probe, run_ffmpeg, to_wav
from .models import Segment, WordTiming
from .reporter import Reporter
from .story_schema import StoryScene, Storyboard, load_storyboard
from .tts import TTSEngine, ToneTTS, make_engine

_FMT = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
ASSET_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
ASSET_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


def find_scene_asset(assets_dir: Path | str, scene_id: int) -> Optional[Path]:
    """Tìm tài nguyên cho cảnh theo thứ tự ưu tiên:
    1. Clip {id:03d}.mp4 / {id}.mp4
    2. Ảnh {id:03d}.png / .jpg / .jpeg / ...
    3. Ảnh {id}.png / .jpg / .jpeg / ...
    """
    assets_dir = Path(assets_dir)
    if not assets_dir.is_dir():
        return None

    # 1. Ưu tiên clip
    for ext in ASSET_VIDEO_EXTS:
        for pattern in (f"{scene_id:03d}{ext}", f"{scene_id}{ext}"):
            p = assets_dir / pattern
            if p.is_file():
                return p
        # Kiểm tra đuôi viết hoa
        for pattern in (f"{scene_id:03d}{ext.upper()}", f"{scene_id}{ext.upper()}"):
            p = assets_dir / pattern
            if p.is_file():
                return p

    # 2. Ảnh tĩnh
    for ext in ASSET_IMAGE_EXTS:
        for pattern in (f"{scene_id:03d}{ext}", f"{scene_id}{ext}"):
            p = assets_dir / pattern
            if p.is_file():
                return p
        # Kiểm tra đuôi viết hoa
        for pattern in (f"{scene_id:03d}{ext.upper()}", f"{scene_id}{ext.upper()}"):
            p = assets_dir / pattern
            if p.is_file():
                return p

    return None


def validate_story_assets(storyboard: Storyboard, assets_dir: Path | str) -> Tuple[Dict[int, Path], List[int]]:
    """Kiểm tra sự tồn tại tài nguyên cho toàn bộ các cảnh trong storyboard.
    
    Trả về (dict[scene_id, file_path], missing_ids).
    """
    assets_dir = Path(assets_dir)
    found: Dict[int, Path] = {}
    missing: List[int] = []

    for sc in storyboard.scenes:
        asset = find_scene_asset(assets_dir, sc.id)
        if asset is not None:
            found[sc.id] = asset
        else:
            missing.append(sc.id)

    return found, missing


def create_tiktok_karaoke_style(
    font_name: str = "Arial",
    font_size: int = 52,
) -> SSAStyle:
    """Tạo kiểu phụ đề Karaoke .ass chuẩn vùng an toàn TikTok 9:16 (MarginV=400, MarginL=80, MarginR=160)."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    # PrimaryColour: Vàng tươi khi đang đọc
    style.primarycolor = Color(255, 230, 0, 0)
    # SecondaryColour: Trắng sáng chờ đọc
    style.secondarycolor = Color(255, 255, 255, 0)
    # OutlineColour: Viền đen rõ nét
    style.outlinecolor = Color(0, 0, 0, 0)
    # BackColour: Bóng mờ
    style.backcolor = Color(0, 0, 0, 128)

    style.borderstyle = 1
    style.outline = 4.0
    style.shadow = 2.0
    style.alignment = Alignment.BOTTOM_CENTER
    style.marginv = 400
    style.marginl = 80
    style.marginr = 160
    return style


def generate_story_karaoke_ass(
    segments: Sequence[Segment],
    out_path: Path | str,
    *,
    font_size: int = 52,
    font_name: str = "Arial",
    use_kf: bool = True,
) -> Path:
    """Sinh file .ass Karaoke 9:16 với PlayResX=1080, PlayResY=1920 và vùng an toàn TikTok."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    subs = SSAFile()
    subs.info["PlayResX"] = 1080
    subs.info["PlayResY"] = 1920

    style_name = "KaraokeTikTok"
    subs.styles[style_name] = create_tiktok_karaoke_style(font_name=font_name, font_size=font_size)

    k_tag = r"\kf" if use_kf else r"\k"

    for seg in segments:
        if not seg.text.strip():
            continue

        start_ms = int(round(seg.start * 1000))
        end_ms = int(round(seg.end * 1000))

        if seg.words:
            w_start_ms = int(round(seg.words[0].start * 1000))
            w_end_ms = int(round(seg.words[-1].end * 1000))
            start_ms = min(start_ms, w_start_ms)
            end_ms = max(end_ms, w_end_ms)

            parts: List[str] = []
            for w in seg.words:
                dur_cs = max(1, int(round(w.duration * 100)))
                parts.append(f"{{{k_tag}{dur_cs}}}{w.word}")
            event_text = " ".join(parts)
        else:
            event_text = seg.text

        subs.events.append(SSAEvent(start=start_ms, end=end_ms, text=event_text, style=style_name))

    subs.save(str(out_path), encoding="utf-8")
    return out_path


def _build_zoompan_filter(motion: str, duration: float) -> str:
    """Xây dựng chuỗi bộ lọc zoompan cho hiệu ứng Ken Burns 9:16 (1080x1920, 30fps)."""
    d = max(1, int(math.ceil(duration * 30)))
    step = 0.15 / max(d, 1)

    if motion == "zoom_in":
        vf_zoom = (
            f"zoompan=z='min(pzoom+{step:.6f},1.25)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s=1080x1920:fps=30"
        )
    elif motion == "zoom_out":
        vf_zoom = (
            f"zoompan=z='if(eq(on,0),1.15,max(1.0,pzoom-{step:.6f}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s=1080x1920:fps=30"
        )
    elif motion == "pan_left":
        vf_zoom = (
            f"zoompan=z='1.15':"
            f"x='(1.0-on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s=1080x1920:fps=30"
        )
    elif motion == "pan_right":
        vf_zoom = (
            f"zoompan=z='1.15':"
            f"x='(on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s=1080x1920:fps=30"
        )
    else:  # "static" hoặc fallback
        vf_zoom = (
            f"zoompan=z='1.0':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s=1080x1920:fps=30"
        )

    return f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,{vf_zoom},setsar=1,format=yuv420p"


def find_sfx_file(sfx_name: str, search_dirs: Sequence[Path]) -> Optional[Path]:
    """Tìm file âm thanh SFX theo tên trong các thư mục chỉ định."""
    if not sfx_name or sfx_name.lower() == "none":
        return None

    for d in search_dirs:
        if not d.is_dir():
            continue
        for ext in (".wav", ".mp3", ".ogg", ".aac", ".m4a"):
            candidate = d / f"{sfx_name}{ext}"
            if candidate.is_file():
                return candidate
    return None


class StoryPipeline:
    """Lõi điều phối dựng video Story 9:16 hoàn chỉnh."""

    def __init__(
        self,
        settings: Optional[DubSettings] = None,
        keys: Optional[Keys] = None,
        reporter: Optional[Reporter] = None,
        *,
        tts_engine: Optional[TTSEngine] = None,
        work_root: Optional[Path] = None,
        cache: Optional[AudioCache] = None,
        sfx_dir: Optional[Path] = None,
    ):
        self.s = settings or DubSettings()
        self.keys = keys or Keys.from_env()
        self.rep = reporter or Reporter()
        self._engine = tts_engine
        self.work_root = Path(work_root) if work_root else WORK_ROOT
        self.cache = cache or AudioCache()
        self.sfx_dir = Path(sfx_dir) if sfx_dir else None
        self._workdir: Optional[Path] = None

    def workdir(self) -> Path:
        if self._workdir is None:
            self._workdir = self.work_root / time.strftime("story_%Y%m%d_%H%M%S")
            self._workdir.mkdir(parents=True, exist_ok=True)
        return self._workdir

    def cleanup(self) -> None:
        if self._workdir and not getattr(self.s, "keep_temp", False):
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workdir = None

    def render_story(
        self,
        storyboard_path_or_obj: Union[Path, str, dict, Storyboard],
        assets_dir: Path | str,
        output_path: Optional[Path | str] = None,
        *,
        bgm_path: Optional[Path | str] = None,
        burn_subtitles: bool = True,
        codec: str = "h264",
        scene_pad: float = 0.25,
        bgm_volume: float = 0.35,
        alignment_data: Optional[Dict[str, Any]] = None,
        full_audio_path: Optional[Path | str] = None,
    ) -> Path:
        """Thực hiện toàn bộ quy trình dựng video Story 9:16 (hỗ trợ cả v1 và v2.0 hybrid)."""
        assets_dir = Path(assets_dir)
        if not assets_dir.is_dir():
            raise DubberError(f"Thư mục tài nguyên không tồn tại: {assets_dir}")

        if isinstance(storyboard_path_or_obj, Storyboard):
            sb = storyboard_path_or_obj
        else:
            sb = load_storyboard(storyboard_path_or_obj)

        if not output_path:
            clean_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in sb.title).strip("_")
            output_path = OUTPUT_DIR / f"{clean_title or 'story'}_916.mp4"
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        work = self.workdir()
        self.rep.log(f"📖 Bắt đầu dựng phim: '{sb.title}' ({len(sb.scenes)} cảnh)")

        # ------------------------------------------------------------- 1. Khớp tài nguyên
        self.rep.progress(5, "Đang kiểm tra tài nguyên ảnh/clip...")
        asset_map, missing = validate_story_assets(sb, assets_dir)
        if missing:
            raise DubberError(f"Thiếu tài nguyên cho {len(missing)} cảnh (ID: {missing}) trong thư mục {assets_dir.name}")
        self.rep.log(f"✓ Đã khớp đủ {len(asset_map)} tài nguyên media cho các cảnh.", "ok")
        self.rep.check_cancel()

        # ------------------------------------------------------------- 2. Xử lý Alignment & Nguồn Âm thanh
        # Áp dụng ElevenLabs alignment nếu có
        if alignment_data is not None:
            words_list = chars_to_words(alignment_data)
            align_scenes_with_words(sb.scenes, words_list)
        elif (assets_dir / "alignment.json").is_file():
            try:
                align_dict = json.loads((assets_dir / "alignment.json").read_text(encoding="utf-8"))
                words_list = chars_to_words(align_dict)
                align_scenes_with_words(sb.scenes, words_list)
            except Exception as e:
                self.rep.log(f"⚠️ Bỏ qua file alignment.json lỗi: {e}", "warn")

        # Kiểm tra nguồn full audio nếu có
        resolved_full_audio: Optional[Path] = None
        if full_audio_path and Path(full_audio_path).is_file():
            resolved_full_audio = Path(full_audio_path).resolve()
        elif (assets_dir / "full_audio.mp3").is_file():
            resolved_full_audio = (assets_dir / "full_audio.mp3").resolve()
        elif (assets_dir / "full_audio.wav").is_file():
            resolved_full_audio = (assets_dir / "full_audio.wav").resolve()
        elif (
            sb.full_script_for_elevenlabs
            and getattr(self.keys, "elevenlabs", None)
            and not isinstance(self._engine, ToneTTS)
            and not any(sc.duration_sec > 0 for sc in sb.scenes)
        ):
            try:
                self.rep.progress(10, "Đang gọi ElevenLabs /with-timestamps cho toàn bộ kịch bản...")
                el_mp3 = work / "elevenlabs_full.mp3"
                voice_id_str = getattr(self._engine, "voice_id", "21m00Tcm4TlvDq8ikWAM") if self._engine else "21m00Tcm4TlvDq8ikWAM"
                align_dict = generate_voice_with_timestamps(
                    self.keys.elevenlabs,
                    voice_id_str,
                    sb.get_full_script(),
                    el_mp3,
                )
                words_list = chars_to_words(align_dict)
                align_scenes_with_words(sb.scenes, words_list)
                resolved_full_audio = el_mp3
            except Exception as e:
                self.rep.log(f"⚠️ ElevenLabs /with-timestamps lỗi ({e}) -> chuyển per-scene TTS", "warn")

        scene_durations: List[float] = []
        scene_audio_files: List[Path] = []
        timeline_segments: List[Segment] = []
        timeline_starts: List[float] = []
        total_scenes = len(sb.scenes)

        voice_track_path = work / "voice_track.wav"

        if resolved_full_audio is not None:
            # Chế độ full audio (từ ElevenLabs hoặc audio dựng sẵn)
            to_wav(resolved_full_audio, voice_track_path, SR, 1)
            audio_info = probe(voice_track_path)
            total_duration = audio_info.duration

            for i, sc in enumerate(sb.scenes):
                timeline_starts.append(sc.start_time)
                if sc.duration_sec > 0:
                    dur_val = sc.duration_sec
                elif sc.end_time > sc.start_time:
                    dur_val = round(sc.end_time - sc.start_time, 3)
                else:
                    dur_val = round(total_duration / max(1, total_scenes), 3)
                scene_durations.append(dur_val)

            self.rep.log(f"✓ Đã nạp track giọng nói tổng: {total_duration:.2f}s", "ok")
        else:
            # Chế độ sinh TTS từng cảnh
            engine = self._engine or make_engine(
                self.s,
                self.keys,
                on_fallback=lambda name, e: self.rep.log(f"⚠️ {name} lỗi ({e}) → chuyển engine dự phòng", "warn"),
            )
            voice_id = getattr(engine, "voice_id", getattr(engine, "voice", ""))
            engine_params = getattr(engine, "params", {})

            clips_dir = work / "audio_scenes"
            clips_dir.mkdir(parents=True, exist_ok=True)

            timeline_cursor = 0.0

            for i, sc in enumerate(sb.scenes):
                self.rep.check_cancel()
                self.rep.progress(10 + int(i * 35 / max(1, total_scenes)), f"Tạo giọng đọc cảnh {i + 1}/{total_scenes}")

                raw_audio = clips_dir / f"raw_scene_{sc.id:03d}{engine.ext}"
                wav_audio = clips_dir / f"scene_{sc.id:03d}.wav"

                key = compute_cache_key(engine.name, voice_id, sc.speech, engine_params)
                cached = self.cache.get(key, engine.ext)

                words: List[WordTiming] = []
                if cached is not None:
                    cached_audio, words = cached
                    shutil.copyfile(str(cached_audio), str(raw_audio))
                else:
                    words = engine.synthesize(sc.speech, raw_audio) or []
                    self.cache.put(key, raw_audio, words, engine.ext)
                    if getattr(engine, "name", "") == "edge":
                        time.sleep(0.35)

                to_wav(raw_audio, wav_audio, SR, 1)
                audio_info = probe(wav_audio)
                audio_dur = audio_info.duration

                # Nếu đã có duration_sec từ alignment thì dùng, không thì lấy audio_dur + scene_pad
                scene_video_dur = sc.duration_sec if sc.duration_sec > 0 else (audio_dur + scene_pad)
                scene_durations.append(scene_video_dur)
                scene_audio_files.append(wav_audio)
                timeline_starts.append(timeline_cursor)

                # Căn mốc WordTiming vào timeline chung
                scene_words = [w.scale_and_shift(speed=1.0, offset=timeline_cursor) for w in words]
                timeline_segments.append(
                    Segment(
                        start=timeline_cursor,
                        end=timeline_cursor + audio_dur,
                        text=sc.speech,
                        words=scene_words,
                    )
                )

                timeline_cursor += scene_video_dur

            total_duration = timeline_cursor
            self.rep.log(f"✓ Đã tạo xong giọng đọc. Tổng thời lượng dự kiến: {total_duration:.2f}s", "ok")

        # ------------------------------------------------------------- 3. Sinh phụ đề Karaoke .ass 9:16
        ass_path = work / "story_karaoke_916.ass"
        has_aligned_words = any(len(getattr(sc, "words", [])) > 0 for sc in sb.scenes)
        if has_aligned_words:
            export_karaoke_ass(sb.scenes, ass_path, max_words_per_line=6)
        else:
            generate_story_karaoke_ass(timeline_segments, ass_path, font_size=52)

        # Đồng thời lưu bản sao .ass ra cạnh file video cuối
        exported_ass = output_path.with_suffix(".ass")
        shutil.copyfile(str(ass_path), str(exported_ass))

        # ------------------------------------------------------------- 4. Render từng cảnh 9:16
        scenes_video_dir = work / "scenes_video"
        scenes_video_dir.mkdir(parents=True, exist_ok=True)
        rendered_scene_videos: List[Path] = []

        for i, sc in enumerate(sb.scenes):
            self.rep.check_cancel()
            self.rep.progress(45 + int(i * 35 / max(1, total_scenes)), f"Render cảnh 9:16 ({i + 1}/{total_scenes})")

            asset_file = asset_map[sc.id]
            dur = scene_durations[i]
            out_clip = scenes_video_dir / f"clip_{sc.id:03d}.mp4"

            is_video = (sc.asset_type == "flow_video") or (asset_file.suffix.lower() in ASSET_VIDEO_EXTS)
            if is_video:
                # Clip video (Google Flow hoặc video file): trim từ 0.0 đến dur, loop nếu dur > clip
                cmd = [
                    "-stream_loop", "-1",
                    "-i", str(asset_file),
                    "-t", f"{dur:.3f}",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "20",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    str(out_clip),
                ]
            else:
                # Ảnh tĩnh: scale/crop và áp Ken Burns zoompan với d = ceil(dur * 30)
                effective_motion = sc.ken_burns or sc.motion
                vf = _build_zoompan_filter(effective_motion, dur)
                cmd = [
                    "-loop", "1",
                    "-t", f"{dur:.3f}",
                    "-i", str(asset_file),
                    "-vf", vf,
                    "-t", f"{dur:.3f}",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "20",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    str(out_clip),
                ]

            res = run_ffmpeg(cmd, check=False)
            if res.returncode != 0:
                raise DubberError(f"Render cảnh {sc.id} thất bại:\n{res.stderr.strip()[-800:]}")

            rendered_scene_videos.append(out_clip)

        self.rep.check_cancel()

        # ------------------------------------------------------------- 5. Concat các cảnh video
        self.rep.progress(82, "Đang ghép nối chuỗi cảnh video 9:16...")
        concat_txt = work / "concat_list.txt"
        with concat_txt.open("w", encoding="utf-8") as f:
            for v_path in rendered_scene_videos:
                rel = v_path.relative_to(work).as_posix()
                f.write(f"file '{rel}'\n")

        raw_full_video = work / "full_video_raw.mp4"
        cmd_concat = [
            "-f", "concat",
            "-safe", "0",
            "-i", "concat_list.txt",
            "-c", "copy",
            str(raw_full_video),
        ]
        res = run_ffmpeg(cmd_concat, cwd=work, check=False)
        if res.returncode != 0:
            raise DubberError(f"Nối video thất bại:\n{res.stderr.strip()[-800:]}")

        # ------------------------------------------------------------- 6. Ghép Voice Track & SFX Track
        self.rep.progress(85, "Đang dựng track âm thanh tổng & SFX...")
        total_samples = int(round(total_duration * SR))

        if resolved_full_audio is None:
            voice_buf = np.zeros(total_samples, dtype=np.float32)
            for i, wav_path in enumerate(scene_audio_files):
                x = fade_edges(read_wav(wav_path))
                start_s = timeline_starts[i]
                idx_start = int(round(start_s * SR))
                if idx_start >= total_samples:
                    break
                idx_end = min(idx_start + x.size, total_samples)
                voice_buf[idx_start:idx_end] = x[: idx_end - idx_start]

            write_wav(voice_track_path, voice_buf)

        # Quét SFX
        sfx_search_dirs = [
            d for d in [
                self.sfx_dir,
                assets_dir / "sfx",
                ROOT_DIR / "assets" / "sfx",
                Path("assets/sfx"),
            ] if d is not None
        ]

        has_sfx = False
        sfx_buf = np.zeros(total_samples, dtype=np.float32)

        for i, sc in enumerate(sb.scenes):
            if sc.sfx and sc.sfx.lower() != "none":
                sfx_file = find_sfx_file(sc.sfx, sfx_search_dirs)
                if sfx_file is not None:
                    try:
                        sfx_wav = work / f"sfx_{i:03d}_{sc.sfx}.wav"
                        to_wav(sfx_file, sfx_wav, SR, 1)
                        sfx_data = fade_edges(read_wav(sfx_wav))
                        idx_start = int(round(timeline_starts[i] * SR))
                        idx_end = min(idx_start + sfx_data.size, total_samples)
                        if idx_end > idx_start:
                            sfx_buf[idx_start:idx_end] += sfx_data[: idx_end - idx_start]
                            has_sfx = True
                    except Exception as e:
                        self.rep.log(f"⚠️ Bỏ qua lỗi đọc SFX '{sc.sfx}': {e}", "warn")

        sfx_track_path = None
        if has_sfx:
            sfx_buf = np.clip(sfx_buf, -1.0, 1.0)
            sfx_track_path = work / "sfx_track.wav"
            write_wav(sfx_track_path, sfx_buf)

        # ------------------------------------------------------------- 7. Trộn BGM Ducking, SFX, Subtitles & Xuất File
        self.rep.progress(90, "Đang trộn âm thanh và xuất video 9:16 tối ưu dung lượng...")

        resolved_bgm = None
        if bgm_path and Path(bgm_path).is_file():
            resolved_bgm = Path(bgm_path).resolve()
        elif sb.bgm:
            candidate_bgm = Path(sb.bgm)
            if not candidate_bgm.is_file():
                candidate_bgm = assets_dir / sb.bgm
            if candidate_bgm.is_file():
                resolved_bgm = candidate_bgm.resolve()

        # Chuẩn bị file ass trong workdir để tránh lỗi ký tự Windows
        ass_work_file = work / "subs.ass"
        shutil.copyfile(str(ass_path), str(ass_work_file))

        # Cấu hình nén video nhẹ dung lượng 12-18MB cho 2-3 phút
        is_h265 = str(codec).lower() in ("h265", "libx265", "hevc")
        if is_h265:
            vcodec_args = ["-c:v", "libx265", "-crf", "26", "-preset", "medium", "-pix_fmt", "yuv420p"]
        else:
            vcodec_args = ["-c:v", "libx264", "-crf", "24", "-preset", "medium", "-pix_fmt", "yuv420p"]

        def build_mix_cmd(include_normalize: bool = True) -> List[str]:
            norm = ":normalize=0" if include_normalize else ""
            inputs = ["-i", str(raw_full_video), "-i", str(voice_track_path)]
            next_idx = 2

            bgm_idx = None
            if resolved_bgm:
                inputs += ["-stream_loop", "-1", "-i", str(resolved_bgm)]
                bgm_idx = next_idx
                next_idx += 1

            sfx_idx = None
            if sfx_track_path:
                inputs += ["-i", str(sfx_track_path)]
                sfx_idx = next_idx
                next_idx += 1

            # Xây dựng filter complex
            fc_parts = []
            if burn_subtitles:
                fc_parts.append("[0:v]ass=subs.ass[vout]")
                v_map = "[vout]"
            else:
                v_map = "0:v:0"

            if bgm_idx is not None and sfx_idx is not None:
                # BGM + Voice + SFX: Duck BGM theo giọng nói rồi trộn 3 luồng
                fc_parts.append(
                    f"[1:a]{_FMT},asplit=2[voice_sc][voice_mix];"
                    f"[{bgm_idx}:a]{_FMT},volume={bgm_volume}[bg];"
                    f"[bg][voice_sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked_bg];"
                    f"[{sfx_idx}:a]{_FMT},volume=0.9[sfx];"
                    f"[ducked_bg][voice_mix][sfx]amix=inputs=3:duration=first{norm}[aout]"
                )
            elif bgm_idx is not None:
                # BGM + Voice
                fc_parts.append(
                    f"[1:a]{_FMT},asplit=2[voice_sc][voice_mix];"
                    f"[{bgm_idx}:a]{_FMT},volume={bgm_volume}[bg];"
                    f"[bg][voice_sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked_bg];"
                    f"[ducked_bg][voice_mix]amix=inputs=2:duration=first{norm}[aout]"
                )
            elif sfx_idx is not None:
                # Voice + SFX
                fc_parts.append(
                    f"[1:a]{_FMT}[voice];"
                    f"[{sfx_idx}:a]{_FMT},volume=0.9[sfx];"
                    f"[voice][sfx]amix=inputs=2:duration=first{norm}[aout]"
                )
            else:
                # Chỉ Voice
                fc_parts.append(f"[1:a]{_FMT}[aout]")

            filter_complex_str = ";".join(fc_parts)
            return [
                *inputs,
                "-filter_complex", filter_complex_str,
                "-map", v_map,
                "-map", "[aout]",
                "-t", f"{total_duration:.3f}",
                "-r", "30",
                *vcodec_args,
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]

        res = run_ffmpeg(build_mix_cmd(True), cwd=work, check=False)
        if res.returncode != 0 and "normalize" in res.stderr:
            res = run_ffmpeg(build_mix_cmd(False), cwd=work, check=False)

        if res.returncode != 0:
            raise DubberError(f"Xuất video Story cuối thất bại:\n{res.stderr.strip()[-900:]}")

        file_size_mb = output_path.stat().st_size / (1024 * 1024)
        self.rep.progress(100, f"Hoàn tất xuất video 9:16 ({file_size_mb:.1f}MB)")
        self.rep.log(f"🎉 Hoàn thành video Story 9:16: {output_path} ({file_size_mb:.1f}MB)", "ok")
        return output_path
