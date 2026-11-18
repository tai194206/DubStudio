"""Storyboard v2: giọng ElevenLabs (một lần gọi, có mốc theo ký tự) -> thời lượng cảnh thật -> MP4.

Khác v1:
  - Toàn bộ kịch bản gửi đi MỘT lần; mốc từng từ lấy từ alignment của ElevenLabs, không ước lượng.
  - Thời lượng cảnh = khoảng từ mốc cảnh này đến mốc cảnh sau, nên không phải đoán.
  - Phụ đề karaoke tô đúng theo mốc từng từ thật.
  - Kiểm tra cứng: số từ phải khớp giữa kịch bản gửi đi và alignment, lệch thì dừng trước khi render.

Dùng:
  python -m story.v2 validate projects/ep02/storyboard_v2.json
  python -m story.v2 script projects/ep02          # ghi full_script.txt để kiểm tra/đối chiếu
  python -m story.v2 build projects/ep02 --voice-id <ID> --api-key-env ELEVENLABS_API_KEY
  python -m story.v2 build projects/ep02 --audio a.mp3 --alignment a.alignment.json   # dùng file đã có
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from story import story as v1

VERSION = "2.0"
ASSET_TYPES = {"flow_video", "static_image"}
TAIL_SECONDS = 0.3          # nghỉ sau câu cuối
FLOW_MAX_SECONDS = 8.0      # clip Flow hiện tại dài 8 giây
KARAOKE_WORDS = 5
TITLE_SECONDS = 3.0
ALIGN_VERSION = 1
MIN_WINDOW = 0.5

Aligner = Callable[[str, Path], Tuple[Path, dict]]


class AlignmentError(v1.StoryError):
    """Mốc thời gian từ ElevenLabs không khớp với kịch bản đã gửi."""


# ============================================================ mô hình & kiểm tra
@dataclass
class V2Scene:
    id: int
    speech: str
    tts_text: str
    asset_type: str
    image_prompt: str
    motion: str
    sfx: str
    on_screen_text: str
    source_note: str = ""


@dataclass
class V2Story:
    title: str
    style_prefix: str
    bgm: str
    scenes: List[V2Scene]
    warnings: List[str] = field(default_factory=list)

    def full_text(self) -> str:
        """Văn bản DUY NHẤT được gửi đi lấy giọng. Mọi kiểm tra mốc đều dựa trên chuỗi này."""
        return " ".join(s.tts_text for s in self.scenes)


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def validate_v2(data: dict) -> V2Story:
    errs: List[str] = []
    warns: List[str] = []
    if str(data.get("version")) != VERSION:
        errs.append(f"version phải là '{VERSION}' (hiện: {data.get('version')!r})")
    if data.get("aspect_ratio") != "9:16":
        errs.append(f"aspect_ratio phải là '9:16' (hiện: {data.get('aspect_ratio')!r})")
    for key in ("title", "scenes"):
        if key not in data:
            errs.append(f"thiếu trường '{key}'")
    if errs:
        raise v1.StoryError("; ".join(errs))

    raw = data["scenes"]
    if not isinstance(raw, list) or not raw:
        raise v1.StoryError("scenes phải là danh sách không rỗng")

    scenes: List[V2Scene] = []
    for i, sc in enumerate(raw, 1):
        try:
            sid = int(sc["id"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"cảnh #{i}: id thiếu hoặc không phải số")
            continue
        speech = _norm(sc.get("speech", ""))
        if not speech:
            errs.append(f"cảnh {sid}: thiếu 'speech'")
            continue
        # tts_text là bản đọc (nếu khác speech): BẮT BUỘC cùng số từ để phụ đề khớp giọng
        tts = _norm(sc.get("tts_text") or speech)
        if len(tts.split()) != len(speech.split()):
            errs.append(f"cảnh {sid}: 'tts_text' có {len(tts.split())} từ, 'speech' có {len(speech.split())} từ; "
                        "phải bằng nhau để phụ đề karaoke khớp giọng")
        asset = sc.get("asset_type", "static_image")
        if asset not in ASSET_TYPES:
            errs.append(f"cảnh {sid}: asset_type '{asset}' không hợp lệ (dùng {sorted(ASSET_TYPES)})")
        motion = sc.get("ken_burns", sc.get("motion", "zoom_in"))
        if motion not in v1.MOTIONS:
            errs.append(f"cảnh {sid}: ken_burns '{motion}' không hợp lệ (dùng {sorted(v1.MOTIONS)})")
        sfx = sc.get("sfx", "none")
        if sfx not in v1.SFX_NAMES:
            errs.append(f"cảnh {sid}: sfx '{sfx}' không hợp lệ (dùng {sorted(v1.SFX_NAMES)})")
        prompt = _norm(sc.get("image_prompt", ""))
        if not prompt:
            warns.append(f"cảnh {sid}: chưa có image_prompt")
        scenes.append(V2Scene(sid, speech, tts, asset, prompt, motion, sfx,
                              str(sc.get("on_screen_text", "")).strip(), str(sc.get("source_note", "")).strip()))

    if errs:
        raise v1.StoryError("\n".join(errs))
    ids = [s.id for s in scenes]
    if len(set(ids)) != len(ids):
        raise v1.StoryError("id cảnh bị trùng")

    total = sum(len(s.tts_text.split()) for s in scenes)
    if not 250 <= total <= 350:
        warns.append(f"tổng {total} từ, khuyến nghị 250-350 (khoảng 90-120 giây)")
    if not 18 <= len(scenes) <= 35:
        warns.append(f"{len(scenes)} cảnh, khuyến nghị 18-35")
    declared = str(data.get("full_script_for_elevenlabs", "")).strip()
    if not declared or declared.endswith("..."):
        warns.append("'full_script_for_elevenlabs' chưa được điền đầy đủ; sẽ dùng bản ghép từ các cảnh")
    elif _norm(declared) != _norm(" ".join(s.tts_text for s in scenes)):
        warns.append("'full_script_for_elevenlabs' khác bản ghép từ các cảnh; sẽ dùng bản ghép từ các cảnh")

    return V2Story(str(data["title"]), str(data.get("style_prefix", "")), str(data.get("bgm", "")), scenes, warns)


def load_v2(path) -> V2Story:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise v1.StoryError(f"{Path(path).name} không phải JSON hợp lệ: {e}") from e
    return validate_v2(data)


def to_v1_story(sb: V2Story) -> v1.Story:
    """Chuyển sang cấu trúc v1 để tái dùng build_mix, render_scene_clip, ASS_HEADER."""
    scenes = [v1.Scene(s.id, s.speech, s.image_prompt, s.motion, s.sfx, s.on_screen_text) for s in sb.scenes]
    return v1.Story(sb.title, "9:16", sb.style_prefix, sb.bgm, scenes)


# ============================================================ mốc từ & ánh xạ cảnh
def chars_to_words(alignment: dict) -> List[dict]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not (len(chars) == len(starts) == len(ends)) or not chars:
        raise AlignmentError("alignment không hợp lệ: số ký tự, mốc bắt đầu và mốc kết thúc không khớp nhau")
    words: List[dict] = []
    cur, ws, we = "", 0.0, 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": ws, "end": we})
                cur = ""
        else:
            if not cur:
                ws = float(s)
            cur += ch
            we = float(e)
    if cur:
        words.append({"word": cur, "start": ws, "end": we})
    return words


def map_scenes(scenes: Sequence[V2Scene], words: Sequence[dict], audio_seconds: float
               ) -> List[Tuple[float, float, Tuple[int, int]]]:
    """Trả về [(bắt đầu cảnh, kết thúc cảnh, (chỉ số từ đầu, cuối+1))]. Cảnh nối khít nhau, không khoảng trống."""
    expected = sum(len(s.tts_text.split()) for s in scenes)
    if len(words) != expected:
        raise AlignmentError(
            f"ElevenLabs trả về {len(words)} từ nhưng kịch bản đã gửi có {expected} từ. "
            "Thường do văn bản đã sửa sau khi gọi API, hoặc gửi nhầm bản. Hãy chạy lại 'script' rồi 'build'.")
    spans: List[Tuple[int, int]] = []
    c = 0
    for s in scenes:
        n = len(s.tts_text.split())
        spans.append((c, c + n))
        c += n
    speech = [(words[a]["start"], words[b - 1]["end"]) for a, b in spans]
    bounds = [0.0]
    for i in range(1, len(scenes)):
        bounds.append((speech[i - 1][1] + speech[i][0]) / 2.0)
    bounds.append(max(audio_seconds, speech[-1][1]) + TAIL_SECONDS)
    return [(bounds[i], bounds[i + 1], spans[i]) for i in range(len(scenes))]


def karaoke_events(tokens: Sequence[str], timings: Sequence[dict]) -> List[Tuple[float, float, str]]:
    """Dòng 5 từ, tô \\kf theo mốc từng từ THẬT của ElevenLabs."""
    events = []
    for i in range(0, len(tokens), KARAOKE_WORDS):
        toks = tokens[i:i + KARAOKE_WORDS]
        tim = timings[i:i + KARAOKE_WORDS]
        start, end = tim[0]["start"], tim[-1]["end"]
        parts, cursor = [], start
        for tok, w in zip(toks, tim):
            cs = max(5, int(round((w["end"] - cursor) * 100)))
            parts.append(f"{{\\kf{cs}}}{v1._ass_escape(tok)}")
            cursor = w["end"]
        events.append((start, end, " ".join(parts)))
    return events


def build_ass_v2(sb: V2Story, windows, words: Sequence[dict]) -> str:
    lines = [v1.ASS_HEADER.rstrip("\n")]
    for sc, (start, end, (a, b)) in zip(sb.scenes, windows):
        tokens = sc.speech.split()
        for ev_start, ev_end, text in karaoke_events(tokens, words[a:b]):
            lines.append(f"Dialogue: 0,{v1.ass_time(ev_start)},{v1.ass_time(ev_end)},Sub,,0,0,0,,{text}")
        if sc.on_screen_text:
            t_end = start + min(TITLE_SECONDS, end - start)
            lines.append(f"Dialogue: 1,{v1.ass_time(start)},{v1.ass_time(t_end)},Title,,0,0,0,,"
                         f"{v1._ass_escape(sc.on_screen_text)}")
    return "\n".join(lines) + "\n"


# ============================================================ ElevenLabs
class ElevenLabsAligner:
    """Gọi /with-timestamps MỘT lần cho cả kịch bản. Kết quả được cache theo SHA-256 của (văn bản, giọng, model)."""
    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_multilingual_v2",
                 stability: float = 0.5, similarity_boost: float = 0.75, max_retries: int = 3,
                 http_post: Optional[Callable] = None, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise v1.StoryError("Chưa có ELEVENLABS_API_KEY")
        if not voice_id:
            raise v1.StoryError("Chưa có voice_id của ElevenLabs")
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self.stability, self.similarity_boost = stability, similarity_boost
        self.max_retries = max_retries
        self._http = http_post
        self._sleep = sleep
        self.calls = 0

    def cache_key(self, text: str) -> str:
        payload = {"v": ALIGN_VERSION, "text": text, "voice": self.voice_id, "model": self.model_id,
                   "stability": self.stability, "similarity": self.similarity_boost}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _post(self, text: str) -> dict:
        if self._http is None:
            import requests
            self._http = lambda url, **kw: requests.post(url, **kw)
        body = {"text": text, "model_id": self.model_id,
                "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity_boost}}
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        url = self.URL.format(voice=self.voice_id)
        for attempt in range(self.max_retries):
            self.calls += 1
            r = self._http(url, headers=headers, json=body, timeout=180)
            if r.status_code == 200:
                return r.json()
            if r.status_code in self.RETRY_STATUS and attempt < self.max_retries - 1:
                self._sleep(2 * (attempt + 1))
                continue
            raise AlignmentError(f"ElevenLabs HTTP {r.status_code}: {str(r.text)[:200]}")
        raise AlignmentError("ElevenLabs không phản hồi sau nhiều lần thử")

    def __call__(self, text: str, cache_dir: Path) -> Tuple[Path, dict]:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = self.cache_key(text)
        mp3, js = cache_dir / f"{key}.mp3", cache_dir / f"{key}.alignment.json"
        if mp3.exists() and js.exists():
            return mp3, json.loads(js.read_text(encoding="utf-8"))
        data = self._post(text)
        if "audio_base64" not in data or "alignment" not in data:
            raise AlignmentError("Phản hồi ElevenLabs thiếu 'audio_base64' hoặc 'alignment'")
        tmp_mp3, tmp_js = mp3.with_suffix(".tmp.mp3"), js.with_suffix(".tmp.json")
        tmp_mp3.write_bytes(base64.b64decode(data["audio_base64"]))
        tmp_js.write_text(json.dumps(data["alignment"], ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_mp3, mp3)
        os.replace(tmp_js, js)
        return mp3, data["alignment"]


# ============================================================ dựng phim
@dataclass
class V2Options:
    sfx_dir: Optional[Path] = None
    cache_dir: Optional[Path] = None
    burn_subtitles: bool = True
    keep_temp: bool = False
    bgm_volume: float = 0.12
    sfx_volume: float = 0.5


def write_full_script(project: Path) -> Path:
    sb = load_v2(Path(project) / "storyboard_v2.json")
    out = Path(project) / "full_script.txt"
    out.write_text(sb.full_text() + "\n", encoding="utf-8")
    return out


def build_v2(project, out, opts: V2Options, aligner: Optional[Aligner] = None,
             audio: Optional[Path] = None, alignment: Optional[dict] = None,
             log: Callable[[str], None] = print) -> Path:
    project = Path(project)
    sb = load_v2(project / "storyboard_v2.json")
    for w in sb.warnings:
        log(f"⚠️ {w}")
    full = sb.full_text()
    assets = project / "assets"
    cache_dir = opts.cache_dir or (project.parent.parent / "cache" / "story")
    sfx_dir = opts.sfx_dir or (project.parent.parent / "assets_global" / "sfx")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Tài nguyên hình ảnh: kiểm tra trước khi gọi API để không mất credit vô ích
    visuals = {s.id: v1.resolve_visual(assets, s.id) for s in sb.scenes}
    for s in sb.scenes:
        if s.asset_type == "flow_video" and visuals[s.id].suffix.lower() != ".mp4":
            log(f"ℹ️ Cảnh {s.id} khai báo flow_video nhưng chưa có {s.id:03d}.mp4, dùng ảnh tĩnh.")

    work = Path(tempfile.mkdtemp(prefix="v2_"))
    try:
        t0 = time.time()
        if audio is not None and alignment is not None:
            src_audio, align = Path(audio), alignment
            log("🎙️ Dùng audio và alignment đã có (không gọi API).")
        else:
            if aligner is None:
                raise v1.StoryError("Cần aligner ElevenLabs, hoặc truyền --audio và --alignment có sẵn.")
            log(f"🎙️ Gọi ElevenLabs một lần cho {len(sb.scenes)} cảnh (có cache)...")
            src_audio, align = aligner(full, cache_dir / "elevenlabs")

        voice_wav = work / "voice.wav"
        v1.to_wav(src_audio, voice_wav)
        voice = v1.read_wav(voice_wav)
        audio_seconds = len(voice) / v1.SR

        words = chars_to_words(align)
        windows = map_scenes(sb.scenes, words, audio_seconds)
        total = windows[-1][1]
        log(f"⏱️ Tổng thời lượng: {total:.1f}s, {len(sb.scenes)} cảnh, {len(words)} từ khớp")

        for s, (start, end, _) in zip(sb.scenes, windows):
            d = end - start
            if d < MIN_WINDOW:
                log(f"⚠️ Cảnh {s.id} chỉ {d:.2f}s")
            if s.asset_type == "flow_video" and d > FLOW_MAX_SECONDS:
                log(f"⚠️ Cảnh {s.id} dài {d:.1f}s nhưng clip Flow chỉ {FLOW_MAX_SECONDS:.0f}s: clip sẽ được lặp. "
                    "Nên tách cảnh hoặc dùng ảnh tĩnh.")

        clips: List[Path] = []
        for s, (start, end, _) in zip(sb.scenes, windows):
            clip = work / f"clip_{s.id:03d}.mp4"
            v1.render_scene_clip(visuals[s.id], s.motion, end - start, clip)
            clips.append(clip)
        concat = work / "concat.txt"
        concat.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        log(f"🎬 Đã dựng {len(clips)} clip cảnh")

        timeline = [(start, end - start) for start, end, _ in windows]
        v1_story = to_v1_story(sb)
        bgm = v1.resolve_bgm(sb.bgm, project, project.parent.parent)
        mix = work / "mix.wav"
        v1_opts = v1.BuildOptions(bgm_volume=opts.bgm_volume, sfx_volume=opts.sfx_volume, sfx_dir=sfx_dir)
        v1.build_mix(voice_wav, mix, total, v1_story, timeline, bgm, sfx_dir, v1_opts)

        subs = work / "subs.ass"
        subs.write_text(build_ass_v2(sb, windows, words), encoding="utf-8")
        log("📝 Đã tạo phụ đề karaoke theo mốc từng từ")

        vf = ["-vf", "ass=subs.ass"] if opts.burn_subtitles else []
        log("🎞️ Đang mã hóa MP4...")
        v1._ffmpeg(["-f", "concat", "-safe", "0", "-i", concat.name, "-i", mix.name, *vf,
                    "-map", "0:v:0", "-map", "1:a:0", "-t", f"{total:.3f}",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-r", str(v1.FPS),
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(out.resolve())],
                   cwd=work)

        timeline_json = out.with_suffix(".timeline.json")
        timeline_json.write_text(json.dumps([
            {"id": s.id, "start": round(st, 3), "end": round(en, 3), "asset": visuals[s.id].name,
             "words": [w["word"] for w in words[a:b]]}
            for s, (st, en, (a, b)) in zip(sb.scenes, windows)], ensure_ascii=False, indent=2), encoding="utf-8")
        size_mb = out.stat().st_size / 1024 / 1024
        log(f"✅ Xong {out.name}: {total:.1f}s, {size_mb:.1f} MB, {time.time() - t0:.0f}s")
        if opts.keep_temp:
            keep = out.parent / f"{out.stem}_work"
            shutil.copytree(work, keep, dirs_exist_ok=True)
            log(f"🗂️ Giữ file tạm tại {keep}")
        return out
    finally:
        if not opts.keep_temp:
            shutil.rmtree(work, ignore_errors=True)


# ============================================================ CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="story.v2", description="Storyboard v2 + ElevenLabs timestamps")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("storyboard")
    s = sub.add_parser("script", help="ghi full_script.txt để đối chiếu")
    s.add_argument("project")
    b = sub.add_parser("build")
    b.add_argument("project")
    b.add_argument("--out", default=None)
    b.add_argument("--voice-id", default="")
    b.add_argument("--model-id", default="eleven_multilingual_v2")
    b.add_argument("--api-key-env", default="ELEVENLABS_API_KEY")
    b.add_argument("--audio", default=None, help="mp3 đã có (bỏ qua API)")
    b.add_argument("--alignment", default=None, help="alignment.json đã có (bỏ qua API)")
    b.add_argument("--no-subs", action="store_true")
    b.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "validate":
            sb = load_v2(args.storyboard)
            print(f"✓ Hợp lệ: '{sb.title}', {len(sb.scenes)} cảnh, {sum(len(x.tts_text.split()) for x in sb.scenes)} từ")
            for w in sb.warnings:
                print(f"⚠️ {w}")
            return 0
        if args.cmd == "script":
            p = write_full_script(args.project)
            print(f"✓ Đã ghi {p}")
            return 0
        project = Path(args.project)
        out = Path(args.out) if args.out else Path("output") / f"{project.name}.mp4"
        opts = V2Options(burn_subtitles=not args.no_subs, keep_temp=args.keep_temp)
        if args.audio and args.alignment:
            align_data = json.loads(Path(args.alignment).read_text(encoding="utf-8"))
            build_v2(project, out, opts, audio=Path(args.audio), alignment=align_data)
        else:
            aligner = ElevenLabsAligner(os.environ.get(args.api_key_env, ""), args.voice_id, args.model_id)
            build_v2(project, out, opts, aligner=aligner)
        return 0
    except v1.StoryError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
