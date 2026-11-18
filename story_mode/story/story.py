"""Story mode: storyboard.json + ảnh/clip -> video dọc 9:16 (1080x1920, 30fps).

Luồng:
  1. Kiểm tra storyboard.json
  2. Giọng đọc từng cảnh (có cache SHA-256) + tăng tốc
  3. Mỗi cảnh -> clip có chuyển động (zoompan theo `motion`), hoặc cắt/lặp clip .mp4
  4. Ghép track giọng theo đúng mốc, trộn BGM (tự hạ khi có giọng) + SFX
  5. Sinh phụ đề karaoke .ass (vùng an toàn 9:16) và xuất MP4 nén

Dùng:
  python -m story validate projects/ep01_example/storyboard.json
  python -m story build projects/ep01_example --engine edge --voice vi-VN-HoaiMyNeural
  python -m story build projects/ep01_example --engine tone          # thử nghiệm, không cần internet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

W, H, FPS = 1080, 1920, 30
SR = 24000                        # tần số track giọng nội bộ
SPEED = 1.15                      # nhịp kể TikTok
TAIL_PAD = 0.2                    # khoảng nghỉ sau mỗi cảnh (giây)
MIN_SCENE = 2.0                   # cảnh ngắn hơn mức này vẫn giữ nguyên, nhưng cảnh báo
MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right", "static"}
SFX_NAMES = {"none", "whoosh", "boom", "pop", "typing", "alarm", "ship_horn"}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm")
KARAOKE_WORDS = 5                 # số từ tối đa mỗi dòng phụ đề
TITLE_SECONDS = 3.0               # on_screen_text hiện trong bao lâu
CACHE_VERSION = 1


class StoryError(Exception):
    """Lỗi có thông điệp rõ ràng cho người dùng."""


# ============================================================ mô hình & kiểm tra
@dataclass
class Scene:
    id: int
    speech: str
    image_prompt: str
    motion: str = "zoom_in"
    sfx: str = "none"
    on_screen_text: str = ""


@dataclass
class Story:
    title: str
    aspect_ratio: str
    style_suffix: str
    bgm: str
    scenes: List[Scene]


def validate(data: dict) -> Tuple[Story, List[str]]:
    """Kiểm tra storyboard. Lỗi cứng -> StoryError. Cảnh báo mềm -> trả về danh sách warnings."""
    errs: List[str] = []
    warns: List[str] = []
    for key in ("title", "aspect_ratio", "scenes"):
        if key not in data:
            errs.append(f"thiếu trường '{key}'")
    if errs:
        raise StoryError("; ".join(errs))
    if data["aspect_ratio"] != "9:16":
        errs.append(f"aspect_ratio phải là '9:16', hiện là '{data['aspect_ratio']}'")
    raw = data["scenes"]
    if not isinstance(raw, list) or not raw:
        raise StoryError("scenes phải là danh sách không rỗng")

    scenes: List[Scene] = []
    for i, sc in enumerate(raw, 1):
        try:
            sid = int(sc["id"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"cảnh #{i}: id thiếu hoặc không phải số")
            continue
        speech = str(sc.get("speech", "")).strip()
        prompt = str(sc.get("image_prompt", "")).strip()
        motion = sc.get("motion", "zoom_in")
        sfx = sc.get("sfx", "none")
        if not speech:
            errs.append(f"cảnh {sid}: thiếu lời thoại 'speech'")
        if not prompt:
            errs.append(f"cảnh {sid}: thiếu 'image_prompt'")
        if motion not in MOTIONS:
            errs.append(f"cảnh {sid}: motion '{motion}' không hợp lệ (dùng {sorted(MOTIONS)})")
        if sfx not in SFX_NAMES:
            errs.append(f"cảnh {sid}: sfx '{sfx}' không hợp lệ (dùng {sorted(SFX_NAMES)})")
        if len(speech.split()) > 28:
            warns.append(f"cảnh {sid}: {len(speech.split())} từ, có thể quá dài cho 4-6 giây")
        scenes.append(Scene(sid, speech, prompt, motion, sfx, str(sc.get("on_screen_text", "")).strip()))

    ids = [s.id for s in scenes]
    if len(set(ids)) != len(ids):
        errs.append("id cảnh bị trùng")
    if errs:
        raise StoryError("\n".join(errs))

    words = sum(len(s.speech.split()) for s in scenes)
    if not 420 <= words <= 550:
        warns.append(f"tổng {words} từ, khuyến nghị 420-550 (khoảng 2-3 phút)")
    if not 25 <= len(scenes) <= 35:
        warns.append(f"{len(scenes)} cảnh, khuyến nghị 25-35")
    if str(data.get("style_suffix", "")).strip() == "":
        warns.append("không có style_suffix (ảnh có thể không đồng bộ phong cách)")
    story = Story(str(data["title"]), "9:16", str(data.get("style_suffix", "")), str(data.get("bgm", "")), scenes)
    return story, warns


def load_storyboard(path) -> Tuple[Story, List[str]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise StoryError(f"storyboard.json không phải JSON hợp lệ: {e}") from e
    return validate(data)


# ============================================================ ffmpeg
def _bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise StoryError(f"Không thấy '{name}' trong PATH. Cài FFmpeg trước.")
    return path


def _ffmpeg(args: Sequence, cwd=None) -> None:
    cmd = [_bin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise StoryError(f"ffmpeg lỗi: {res.stderr.strip()[-700:]}")


def _probe(path) -> str:
    res = subprocess.run([_bin("ffmpeg"), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.stderr


def media_duration(path) -> float:
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", _probe(path))
    if not m:
        raise StoryError(f"Không đọc được thời lượng: {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def has_stream(path, kind: str) -> bool:
    return bool(re.search(rf"Stream #\d+:\d+.*: {kind}:", _probe(path)))


def atempo_chain(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def to_wav(src, dst) -> None:
    _ffmpeg(["-i", src, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst])


# ============================================================ wav & âm thanh (numpy)
def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise StoryError(f"WAV phải 16-bit: {path}")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        return x


def write_wav(path, x: np.ndarray, sr: int = SR) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def trim_silence(x: np.ndarray, threshold: float = 0.012, keep_ms: float = 30.0) -> np.ndarray:
    idx = np.flatnonzero(np.abs(x) > threshold)
    if idx.size == 0:
        return x[:0]
    keep = int(SR * keep_ms / 1000)
    return x[max(idx[0] - keep, 0): idx[-1] + keep + 1]


def fade_edges(x: np.ndarray, ms: float = 8.0) -> np.ndarray:
    n = min(int(SR * ms / 1000), x.size // 2)
    if n <= 0:
        return x
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


# ============================================================ giọng đọc & cache
SynthFn = Callable[[str, Path], None]


def synth_tone(text: str, path: Path) -> None:
    """Giọng giả (sóng sin) để thử nghiệm không cần mạng. Độ dài tỉ lệ với số ký tự."""
    dur = max(0.4, len(text) / 14.0)
    t = np.arange(int(SR * dur)) / SR
    write_wav(path, (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32))


def make_synth(engine: str, voice: str) -> SynthFn:
    if engine == "tone":
        return synth_tone
    if engine == "edge":
        return lambda text, path: _synth_edge(text, path, voice)
    raise StoryError(f"engine không hỗ trợ: {engine} (dùng tone | edge, hoặc đặt file trong assets/audio/)")


def _synth_edge(text: str, path: Path, voice: str) -> None:
    import asyncio

    try:
        import edge_tts
    except ImportError as e:
        raise StoryError("Thiếu edge-tts. Chạy: pip install edge-tts") from e
    mp3 = Path(str(path) + ".mp3")
    last: Optional[Exception] = None
    for attempt in range(4):                       # máy chủ Edge hay từ chối khi gửi dày
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(str(mp3)))
            if mp3.stat().st_size == 0:
                raise RuntimeError("No audio was received")
            to_wav(mp3, path)
            mp3.unlink(missing_ok=True)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise StoryError(f"Edge-TTS thất bại sau nhiều lần thử: {last}")


def speech_cache_key(text: str, engine: str, voice: str) -> str:
    """Khóa cache chỉ gồm thứ ảnh hưởng tới âm thanh THÔ. Tốc độ áp sau nên không nằm trong khóa."""
    payload = {"v": CACHE_VERSION, "text": text, "engine": engine, "voice": voice}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def cached_speech(text: str, engine: str, voice: str, synth: SynthFn, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / f"{speech_cache_key(text, engine, voice)}.wav"
    if dst.exists() and dst.stat().st_size > 44:
        return dst
    tmp = cache_dir / f"{dst.stem}.tmp.wav"
    synth(text, tmp)
    x = trim_silence(read_wav(tmp))
    if x.size == 0:
        raise StoryError(f"Giọng đọc rỗng cho câu: {text[:40]!r}")
    write_wav(tmp, x)
    os.replace(tmp, dst)                            # ghi nguyên tử: ngắt giữa chừng không để file dở
    return dst


def speed_up(src: Path, dst: Path, speed: float) -> Path:
    if abs(speed - 1.0) < 1e-3:
        shutil.copy(src, dst)
        return dst
    _ffmpeg(["-i", src, "-filter:a", atempo_chain(speed), "-c:a", "pcm_s16le", dst])
    return dst


# ============================================================ tài nguyên hình ảnh
def resolve_visual(assets_dir: Path, sid: int) -> Path:
    for ext in VIDEO_EXTS + IMAGE_EXTS:
        p = assets_dir / f"{sid:03d}{ext}"
        if p.exists():
            return p
    raise StoryError(f"cảnh {sid}: thiếu {sid:03d}.png (hoặc .mp4) trong {assets_dir}")


def resolve_audio_override(assets_dir: Path, sid: int) -> Optional[Path]:
    for ext in (".wav", ".mp3", ".m4a"):
        p = assets_dir / "audio" / f"{sid:03d}{ext}"
        if p.exists():
            return p
    return None


def ken_burns(motion: str, frames: int) -> str:
    """Biểu thức zoompan. Trả về chuỗi filter hoàn chỉnh. `on` là số frame hiện tại."""
    n = max(frames, 1)
    center_y = "ih/2-(ih/zoom/2)"
    if motion == "zoom_in":
        z, x = f"1+0.25*on/{n}", "iw/2-(iw/zoom/2)"
    elif motion == "zoom_out":
        z, x = f"1.25-0.25*on/{n}", "iw/2-(iw/zoom/2)"
    elif motion == "pan_left":
        z, x = "1.2", f"(iw-iw/zoom)*(1-on/{n})"
    elif motion == "pan_right":
        z, x = "1.2", f"(iw-iw/zoom)*on/{n}"
    else:
        z, x = "1", "iw/2-(iw/zoom/2)"
    return f"zoompan=z='{z}':x='{x}':y='{center_y}':d={n}:s={W}x{H}:fps={FPS}"


def render_scene_clip(visual: Path, motion: str, duration: float, out: Path) -> None:
    frames = max(1, round(duration * FPS))
    if visual.suffix.lower() in IMAGE_EXTS:
        # Phóng to trước để chuyển động mượt hơn (zoompan hay rung ở độ phân giải thấp)
        vf = ("scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
              + ken_burns(motion, frames) + ",setsar=1,format=yuv420p")
        args = ["-loop", "1", "-i", visual, "-vf", vf, "-frames:v", frames]
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,format=yuv420p")
        args = ["-stream_loop", "-1", "-i", visual, "-t", f"{duration:.3f}", "-vf", vf]
    _ffmpeg([*args, "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-r", str(FPS), out])


# ============================================================ timeline & ASS
def plan_timeline(durations: Sequence[float]) -> List[Tuple[float, float]]:
    out, t = [], 0.0
    for d in durations:
        out.append((t, d))
        t += d
    return out


def chunk_words(text: str, size: int = KARAOKE_WORDS) -> List[List[str]]:
    words = text.split()
    return [words[i:i + size] for i in range(0, len(words), size)]


def karaoke_events(text: str, start: float, duration: float) -> List[Tuple[float, float, str]]:
    """Trả về [(bắt đầu, kết thúc, nội dung ASS)] cho từng dòng 3-5 từ, tô màu theo tỉ lệ ký tự."""
    chunks = chunk_words(text)
    total_chars = sum(len(w) for w in text.split()) or 1
    events, t = [], start
    for words in chunks:
        chars = sum(len(w) for w in words)
        span = duration * chars / total_chars
        parts = []
        for w in words:
            cs = max(1, round(span * 100 * len(w) / max(chars, 1)))
            parts.append(f"{{\\kf{cs}}}{_ass_escape(w)}")
        events.append((t, t + span, " ".join(parts)))
        t += span
    return events


def _ass_escape(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", " ")


def ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Arial,92,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,400,1
Style: Title,Arial,104,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,9,4,8,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(scenes: Sequence[Scene], timeline: Sequence[Tuple[float, float]], speech_dur: Sequence[float]) -> str:
    lines = [ASS_HEADER.rstrip("\n")]
    for sc, (start, dur), sp in zip(scenes, timeline, speech_dur):
        for a, b, text in karaoke_events(sc.speech, start, sp):
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Sub,,0,0,0,,{text}")
        if sc.on_screen_text:
            end = start + min(TITLE_SECONDS, dur)
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Title,,0,0,0,,{_ass_escape(sc.on_screen_text)}")
    return "\n".join(lines) + "\n"


# ============================================================ dựng phim
@dataclass
class BuildOptions:
    engine: str = "tone"
    voice: str = "vi-VN-HoaiMyNeural"
    speed: float = SPEED
    bgm_volume: float = 0.12
    sfx_volume: float = 0.5
    sfx_dir: Optional[Path] = None
    cache_dir: Optional[Path] = None
    burn_subtitles: bool = True
    keep_temp: bool = False


def resolve_bgm(bgm: str, project: Path, repo_root: Path) -> Optional[Path]:
    """Tìm nhạc nền: đường dẫn tuyệt đối, hoặc tương đối với thư mục dự án, hoặc với gốc repo
    (ví dụ 'assets_global/bgm/mystery.mp3' trong storyboard)."""
    if not bgm:
        return None
    if Path(bgm).is_absolute():
        return Path(bgm) if Path(bgm).exists() else None
    for base in (project, repo_root):
        p = base / bgm
        if p.exists():
            return p
    return None


def _resolve_bgm(story: Story, project: Path, opts: BuildOptions) -> Optional[Path]:
    p = resolve_bgm(story.bgm, project, project.parent.parent)
    if story.bgm and p is None:
        print(f"⚠️ Không thấy nhạc nền '{story.bgm}', dựng không có BGM.")
    return p


def build(project: Path, out: Path, opts: BuildOptions, log: Callable[[str], None] = print) -> Path:
    project = Path(project)
    story, warns = load_storyboard(project / "storyboard.json")
    for w in warns:
        log(f"⚠️ {w}")
    assets = project / "assets"
    cache_dir = opts.cache_dir or (project.parent.parent / "cache" / "story")
    sfx_dir = opts.sfx_dir or (project.parent.parent / "assets_global" / "sfx")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1. Kiểm tra toàn bộ tài nguyên trước khi tốn thời gian render
    visuals = {sc.id: resolve_visual(assets, sc.id) for sc in story.scenes}
    missing_sfx = sorted({sc.sfx for sc in story.scenes if sc.sfx != "none" and not (sfx_dir / f"{sc.sfx}.wav").exists()})
    if missing_sfx:
        log(f"⚠️ Chưa có file SFX {missing_sfx} trong {sfx_dir}: các cảnh đó sẽ không có hiệu ứng âm thanh.")

    work = Path(tempfile.mkdtemp(prefix="story_"))
    try:
        t0 = time.time()
        synth = make_synth(opts.engine, opts.voice)

        # 2. Giọng đọc từng cảnh
        log(f"🎙️ Tạo giọng đọc {len(story.scenes)} cảnh (engine={opts.engine})...")
        speech_files: List[Path] = []
        for sc in story.scenes:
            override = resolve_audio_override(assets, sc.id)
            if override:
                raw = work / f"raw_{sc.id:03d}.wav"
                if override.suffix.lower() == ".wav":
                    shutil.copy(override, raw)
                else:
                    to_wav(override, raw)
                raw_wav = write_trimmed(raw)
            else:
                raw_wav = cached_speech(sc.speech, opts.engine, opts.voice, synth, cache_dir)
            fast = work / f"speech_{sc.id:03d}.wav"
            speed_up(raw_wav, fast, opts.speed)
            speech_files.append(fast)
        speech_arrays = [read_wav(p) for p in speech_files]
        speech_dur = [len(x) / SR for x in speech_arrays]
        for sc, d in zip(story.scenes, speech_dur):
            if d < MIN_SCENE:
                log(f"ℹ️ Cảnh {sc.id} chỉ {d:.1f}s, khá ngắn.")

        # 3. Thời lượng cảnh = lời + nghỉ thở; timeline tuyệt đối
        durations = [d + TAIL_PAD for d in speech_dur]
        timeline = plan_timeline(durations)
        total = timeline[-1][0] + timeline[-1][1]
        log(f"⏱️ Tổng thời lượng: {total:.1f}s ({total / 60:.2f} phút), {len(story.scenes)} cảnh")

        # 4. Clip từng cảnh (chuyển động)
        clips: List[Path] = []
        for sc, (_, d) in zip(story.scenes, timeline):
            clip = work / f"clip_{sc.id:03d}.mp4"
            render_scene_clip(visuals[sc.id], sc.motion, d, clip)
            clips.append(clip)
        log(f"🎬 Đã dựng {len(clips)} clip cảnh")
        concat_list = work / "concat.txt"
        concat_list.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")

        # 5. Track giọng theo mốc thời gian
        voice = np.zeros(int(round(total * SR)) + SR, dtype=np.float32)
        for (start, _), x in zip(timeline, speech_arrays):
            i = int(round(start * SR))
            seg = fade_edges(x)
            voice[i:i + seg.size] += seg[: max(0, voice.size - i)]
        voice_wav = work / "voice.wav"
        write_wav(voice_wav, voice)

        # 6. Trộn: BGM (tự hạ khi có giọng) + SFX đúng mốc
        mix = work / "mix.wav"
        build_mix(voice_wav, mix, total, story, timeline, _resolve_bgm(story, project, opts), sfx_dir, opts)

        # 7. Phụ đề karaoke + xuất MP4
        subs_name = "subs.ass"
        (work / subs_name).write_text(build_ass(story.scenes, timeline, speech_dur), encoding="utf-8")
        log(f"📝 Đã tạo phụ đề karaoke ({len(story.scenes)} cảnh)")

        vf = []
        if opts.burn_subtitles:
            vf = ["-vf", f"ass={subs_name}"]          # chạy trong thư mục tạm: đường dẫn tương đối, an toàn trên Windows
        cmd = ["-f", "concat", "-safe", "0", "-i", concat_list.name, "-i", mix.name, *vf,
               "-map", "0:v:0", "-map", "1:a:0", "-t", f"{total:.3f}",
               "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out.resolve())]
        log("🎞️ Đang mã hóa MP4 (preset slow, có thể mất vài phút)...")
        _ffmpeg(cmd, cwd=work)
        size_mb = out.stat().st_size / 1024 / 1024
        log(f"✅ Xong {out.name}: {total:.1f}s, {size_mb:.1f} MB, {time.time() - t0:.0f}s")
        if size_mb > 25:
            log("⚠️ File lớn hơn 25 MB: giảm -crf (ví dụ 26) hoặc bitrate âm thanh.")
        if opts.keep_temp:
            keep = out.parent / f"{out.stem}_work"
            shutil.copytree(work, keep, dirs_exist_ok=True)
            log(f"🗂️ Giữ file tạm tại {keep}")
        return out
    finally:
        if not opts.keep_temp:
            shutil.rmtree(work, ignore_errors=True)


def write_trimmed(raw: Path) -> Path:
    x = trim_silence(read_wav(raw))
    if x.size == 0:
        raise StoryError(f"File giọng đọc rỗng: {raw}")
    write_wav(raw, x)
    return raw


def build_mix(voice_wav: Path, out: Path, total: float, story: Story, timeline, bgm: Optional[Path],
              sfx_dir: Path, opts: BuildOptions) -> None:
    inputs: List[str] = ["-i", str(voice_wav)]
    filters: List[str] = []
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    filters.append(f"[0:a]{fmt},volume=1.0[vox]")
    labels = ["[vox]"]
    cur = "[vox]"

    idx = 1
    if bgm is not None:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        filters.append(f"[{idx}:a]atrim=0:{total:.3f},{fmt},volume={opts.bgm_volume}[b]")
        # Một nhãn FFmpeg chỉ dùng được một lần: tách giọng thành 2 nhánh (sidechain và đầu vào trộn)
        filters.append("[vox]asplit=2[vox_sc][vox_mix]")
        filters.append("[b][vox_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[bd]")
        filters.append("[vox_mix][bd]amix=inputs=2:duration=first:normalize=0[m0]")
        cur = "[m0]"
        idx += 1

    sfx_inputs = []
    for sc, (start, _) in zip(story.scenes, timeline):
        if sc.sfx == "none":
            continue
        f = sfx_dir / f"{sc.sfx}.wav"
        if f.exists():
            sfx_inputs.append((f, start))
    for f, start in sfx_inputs:
        inputs += ["-i", str(f)]
        ms = int(round(start * 1000))
        filters.append(f"[{idx}:a]{fmt},adelay={ms}|{ms},volume={opts.sfx_volume}[s{idx}]")
        labels.append(f"[s{idx}]")
        idx += 1

    if sfx_inputs:
        n = len(sfx_inputs) + 1
        filters.append(f"{cur}{''.join(labels[1:])}amix=inputs={n}:duration=first:normalize=0[aout]")
    else:
        filters.append(f"{cur}anull[aout]")
    chain = ";".join(filters)
    _ffmpeg([*inputs, "-filter_complex", chain, "-map", "[aout]", "-t", f"{total:.3f}",
             "-c:a", "pcm_s16le", str(out)])


# ============================================================ CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="story", description="Dựng video lịch sử AI 9:16 từ storyboard.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="Kiểm tra storyboard.json")
    v.add_argument("storyboard")

    b = sub.add_parser("build", help="Dựng video từ thư mục dự án")
    b.add_argument("project", help="thư mục chứa storyboard.json và assets/")
    b.add_argument("--out", default=None, help="file MP4 đầu ra (mặc định: output/<tên dự án>.mp4)")
    b.add_argument("--engine", default="tone", choices=["tone", "edge"])
    b.add_argument("--voice", default="vi-VN-HoaiMyNeural")
    b.add_argument("--speed", type=float, default=SPEED)
    b.add_argument("--no-subs", action="store_true", help="không đốt phụ đề vào video")
    b.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "validate":
            story, warns = load_storyboard(args.storyboard)
            words = sum(len(s.speech.split()) for s in story.scenes)
            print(f"✓ Hợp lệ: '{story.title}', {len(story.scenes)} cảnh, {words} từ")
            for w in warns:
                print(f"⚠️ {w}")
            return 0
        project = Path(args.project)
        out = Path(args.out) if args.out else Path("output") / f"{project.name}.mp4"
        opts = BuildOptions(engine=args.engine, voice=args.voice, speed=args.speed,
                            burn_subtitles=not args.no_subs, keep_temp=args.keep_temp)
        build(project, out, opts)
        return 0
    except StoryError as e:
        print(f"❌ {e}")
        return 1
