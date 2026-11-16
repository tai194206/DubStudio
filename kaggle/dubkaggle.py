"""dubkaggle - lõi xử lý cho notebook Kaggle của DubStudio.

Giai đoạn 1 (notebook 01): video -> transcript.json (có mốc từng từ) + SRT + script.txt + báo cáo QC.
Giai đoạn 2 (notebook 02): transcript -> dịch Gemini -> kiểm tra tốc độ đọc -> rút gọn câu quá dài.

Toàn bộ logic thuần (không cần GPU, không cần mạng) được test bằng pytest. Phần gọi model thật
(WhisperASR, make_gemini_generate) chỉ là lớp mỏng bọc thư viện bên ngoài.

Chỉ dùng với video bạn sở hữu hoặc được phép sử dụng.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "1.0.0"
SCHEMA = "dubstudio.transcript/1"
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv")
NO_SPACE_LANGS = {"zh", "ja", "th", "yue", "lo", "my", "km"}
SENT_END = "。！？!?….\u2026"
CLAUSE_END = "，、；：,;:"

# Tốc độ nói của nguồn (ký tự KHÔNG tính khoảng trắng/dấu câu, trên giây) vượt mức này thì cảnh báo.
FAST_LIMIT_SRC = {"zh": 8.0, "ja": 9.0, "ko": 9.0, "th": 12.0}
FAST_LIMIT_DEFAULT = 20.0
# Tốc độ đọc của giọng TTS đích (ký tự CÓ khoảng trắng, trên giây, ở tốc độ 1.0x).
# Đây là ước lượng bảo thủ: hãy đo thực tế bằng calibrate_cps() rồi điền lại.
TARGET_CPS = {"vi": 15.0, "en": 15.0, "zh": 5.0, "ja": 7.0, "ko": 8.0, "th": 11.0,
              "id": 14.0, "es": 15.0, "fr": 15.0, "de": 14.0}
LANG_NAMES = {"vi": "Vietnamese", "en": "English", "zh": "Simplified Chinese", "ja": "Japanese",
              "ko": "Korean", "th": "Thai", "id": "Indonesian", "es": "Spanish", "fr": "French",
              "de": "German"}
AUTO_MAX_CHARS = {"zh": 36, "ja": 40, "ko": 45, "th": 60}
AUTO_MAX_CHARS_DEFAULT = 110

# Câu Whisper hay "bịa" ra khi gặp nhạc nền/im lặng. Chỉ bị bỏ khi độ tin cậy cũng thấp.
DEFAULT_HALLUCINATIONS = (
    r"字幕(由|制作|提供|组)", r"请不吝.{0,8}(点赞|订阅|转发|打赏)", r"(点赞|订阅|转发).{0,6}(打赏|支持)",
    r"明镜与点点", r"Amara\.org", r"感谢(您的)?(观看|收看)", r"(感谢|谢谢)大家",
    r"thanks? for watching", r"subtitles? (by|from)", r"subscribe to (my|the|our) channel",
    r"cảm ơn các bạn đã (xem|theo dõi)", r"hãy (đăng ký|subscribe)",
    r"ご視聴ありがとうございました", r"시청해 주셔서 감사합니다",
)


def log(msg: str) -> None:
    print(msg, flush=True)


# ============================================================ kiểu dữ liệu
@dataclass
class Word:
    word: str
    start: float
    end: float
    prob: Optional[float] = None


@dataclass
class Seg:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)
    id: int = 0
    src: str = ""                      # lời gốc (dùng ở giai đoạn dịch)
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None
    compression_ratio: Optional[float] = None
    flags: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Config:
    # --- đầu vào / đầu ra
    video_path: str = ""
    work_dir: str = "/kaggle/working/work"
    out_dir: str = "/kaggle/working/output"
    # --- nhận diện
    language: str = "zh"               # "auto" để tự nhận diện
    model_size: str = "large-v3"       # large-v3 | medium | small ...
    device: str = "auto"               # auto | cuda | cpu
    compute_type: str = "auto"         # auto: float16 trên GPU, int8 trên CPU
    beam_size: int = 5
    hotwords: str = ""                 # tên riêng cần nhận đúng, vd "哆啦A梦, 大雄, 静香"
    initial_prompt: str = ""
    vad_filter: bool = True
    vad_min_silence_ms: int = 500
    condition_on_previous_text: bool = False
    # --- âm thanh
    normalize_audio: bool = True
    separate_vocals: bool = False      # tách giọng khỏi nhạc nền bằng Demucs (tùy chọn, nặng)
    # --- hậu xử lý
    drop_hallucinations: bool = True
    min_seg_seconds: float = 0.6
    merge_gap: float = 0.35
    max_seg_seconds: float = 8.0
    max_chars: int = 0                 # 0 = tự chọn theo ngôn ngữ
    # --- quét lại khoảng trống để tìm lời bị bỏ sót
    recheck_gaps: bool = True
    gap_recheck_min: float = 4.0
    gap_warn: float = 6.0
    # --- ngưỡng QC
    low_logprob: float = -1.0
    no_speech_flag: float = 0.5
    repetition_ratio: float = 2.4

    def lang_code(self, detected: Optional[str] = None) -> str:
        code = detected if self.language in ("", "auto") and detected else self.language
        return (code or "auto").split("-")[0].lower()


# ============================================================ tiện ích văn bản / thời gian
def fmt_srt(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def fmt_bracket(t: float) -> str:
    return fmt_srt(t).replace(",", ".")


def norm_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def count_chars(text: str) -> int:
    """Số ký tự 'nói được' (bỏ khoảng trắng và dấu câu). Dùng cho tốc độ nói của nguồn."""
    return len(norm_text(text))


def speech_len(text: str) -> int:
    """Độ dài để ước lượng thời gian TTS đọc (giữ khoảng trắng, gộp nhiều khoảng trắng)."""
    return len(re.sub(r"\s+", " ", text).strip())


def join_words(words: Sequence[Word], lang: str) -> str:
    parts = [w.word.strip() for w in words if w.word.strip()]
    return ("" if lang in NO_SPACE_LANGS else " ").join(parts)


def join_text(a: str, b: str, lang: str) -> str:
    return (a + b) if lang in NO_SPACE_LANGS else (a.rstrip() + " " + b.lstrip())


def max_chars_for(cfg: Config, lang: str) -> int:
    return cfg.max_chars or AUTO_MAX_CHARS.get(lang, AUTO_MAX_CHARS_DEFAULT)


# ============================================================ FFmpeg
def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("Không tìm thấy ffmpeg (Kaggle có sẵn; máy local cần cài thêm)")
    return path


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe_duration(path) -> float:
    res = _run([ffmpeg_bin(), "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        raise RuntimeError(f"Không đọc được file media: {path}")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def has_audio_stream(path) -> bool:
    res = _run([ffmpeg_bin(), "-hide_banner", "-i", str(path)])
    return bool(re.search(r"Stream #\d+:\d+.*: Audio:", res.stderr))


def extract_audio(src, dst, sample_rate: int = 16000, normalize: bool = True) -> Path:
    """Tách âm thanh mono 16 kHz. loudnorm giúp Whisper nghe rõ đoạn nói nhỏ."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn",
           "-ac", "1", "-ar", str(sample_rate)]
    if normalize:
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi khi tách âm thanh: {res.stderr.strip()[-500:]}")
    return Path(dst)


def cut_audio(src, dst, start: float, end: float) -> Path:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
           "-to", f"{end:.3f}", "-i", str(src), "-c:a", "pcm_s16le", str(dst)]
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi khi cắt đoạn: {res.stderr.strip()[-500:]}")
    return Path(dst)


def separate_vocals(wav_in, work_dir) -> Optional[Path]:
    """Tách giọng nói khỏi nhạc nền bằng Demucs. Trả None nếu thất bại (khi đó dùng audio gốc)."""
    out = Path(work_dir) / "demucs"
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out), str(wav_in)]
    try:
        res = _run(cmd)
    except FileNotFoundError:
        return None
    found = list(out.rglob("vocals.wav"))
    if res.returncode != 0 or not found:
        log(f"⚠️ Demucs thất bại, dùng audio gốc. ({(res.stderr or res.stdout).strip()[-200:]})")
        return None
    mono = Path(work_dir) / "vocals_16k.wav"
    extract_audio(found[0], mono, 16000, normalize=True)
    return mono


# ============================================================ ASR (faster-whisper)
def _load_audio_for_whisper(audio_path: str):
    """Đọc WAV 16-bit mono 16kHz thành mảng float32 để bỏ qua PyAV (tránh lỗi metadata_errors).
    Nếu thiếu numpy hoặc file giả lập khi test offline, tự trả lại đường dẫn gốc.
    """
    p = Path(audio_path)
    if not p.is_file() or p.suffix.lower() != ".wav":
        return audio_path
    try:
        import wave
        import numpy as np
    except ImportError:
        return audio_path
    try:
        with wave.open(str(p), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
                return audio_path
            raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001
        return audio_path


class WhisperASR:
    """Lớp mỏng quanh faster-whisper. Tự hạ cấp GPU float16 -> GPU int8 -> CPU int8 khi gặp lỗi CUDA."""

    FALLBACKS = [("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")]

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = None
        self.device = ""
        self.compute_type = ""
        self.info: Dict[str, object] = {}
        self._chain = self._build_chain()

    def _build_chain(self):
        c = self.cfg
        if c.device == "cpu":
            return [("cpu", c.compute_type if c.compute_type != "auto" else "int8")]
        first = ("cuda", c.compute_type if c.compute_type != "auto" else "float16")
        rest = [f for f in self.FALLBACKS if f != first]
        return [first] + rest

    def _load(self) -> None:
        from faster_whisper import WhisperModel  # import trễ: máy không có thư viện vẫn import được module

        last = None
        while self._chain:
            device, ctype = self._chain[0]
            try:
                log(f"🚀 Tải model {self.cfg.model_size} ({device}, {ctype})...")
                self.model = WhisperModel(self.cfg.model_size, device=device, compute_type=ctype)
                self.device, self.compute_type = device, ctype
                return
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"⚠️ Không tải được ({device}/{ctype}): {e}")
                self._chain.pop(0)
        raise RuntimeError(f"Không tải được model Whisper ở bất kỳ cấu hình nào: {last}")

    def _run_once(self, audio_path: str, offset: float, vad: bool) -> List[Seg]:
        c = self.cfg
        kwargs = dict(
            language=None if c.language in ("", "auto") else c.language,
            beam_size=c.beam_size,
            word_timestamps=True,
            condition_on_previous_text=c.condition_on_previous_text,
            vad_filter=vad,
        )
        if vad:
            kwargs["vad_parameters"] = dict(min_silence_duration_ms=c.vad_min_silence_ms)
        if c.initial_prompt:
            kwargs["initial_prompt"] = c.initial_prompt
        if c.hotwords:
            kwargs["hotwords"] = c.hotwords
        audio_input = _load_audio_for_whisper(audio_path)
        try:
            seg_iter, info = self.model.transcribe(audio_input, **kwargs)
        except TypeError:  # bản faster-whisper cũ không có hotwords
            kwargs.pop("hotwords", None)
            seg_iter, info = self.model.transcribe(audio_input, **kwargs)
        if not self.info:
            self.info = {"language": info.language, "language_probability": float(info.language_probability),
                         "duration": float(getattr(info, "duration", 0.0))}
            log(f"🌐 Ngôn ngữ: {info.language} (tin cậy {info.language_probability:.0%})")
        out: List[Seg] = []
        for s in seg_iter:  # generator: lỗi CUDA thường nhổ ra ở đây
            text = (s.text or "").strip()
            if not text:
                continue
            words = [Word((w.word or "").strip(), w.start + offset, w.end + offset, getattr(w, "probability", None))
                     for w in (s.words or []) if (w.word or "").strip()]
            seg_start = words[0].start if words else (s.start + offset)
            seg_end = words[-1].end if words else (s.end + offset)
            if seg_end <= seg_start:
                seg_start, seg_end = s.start + offset, s.end + offset
            out.append(Seg(start=seg_start, end=seg_end, text=text, words=words,
                           avg_logprob=s.avg_logprob, no_speech_prob=s.no_speech_prob,
                           compression_ratio=s.compression_ratio))
        return out

    def transcribe(self, audio_path, offset: float = 0.0, vad: Optional[bool] = None) -> List[Seg]:
        use_vad = self.cfg.vad_filter if vad is None else vad
        if self.model is None:
            self._load()
        while True:
            try:
                return self._run_once(str(audio_path), offset, use_vad)
            except RuntimeError as e:
                msg = str(e).lower()
                if self.device == "cuda" and any(k in msg for k in ("cudnn", "cublas", "cuda", "out of memory")) \
                        and len(self._chain) > 1:
                    log(f"⚠️ Lỗi GPU khi chạy ({e}). Hạ cấp cấu hình và thử lại...")
                    self._chain.pop(0)
                    self.model = None
                    self._load()
                    continue
                raise


# ============================================================ hậu xử lý
def is_suspect_phrase(text: str, patterns: Sequence[str] = DEFAULT_HALLUCINATIONS) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def drop_hallucinations(segs: Sequence[Seg], cfg: Config, strict: bool = False
                        ) -> Tuple[List[Seg], List[Seg]]:
    """Bỏ câu 'bịa'. Quy tắc (chỉ khi có số liệu): câu mẫu + (logprob thấp | no_speech cao);
    no_speech rất cao + logprob thấp; nén tỷ số lặp > 3.5. strict=True dùng khi quét lại khoảng trống."""
    kept, dropped = [], []
    for s in segs:
        lp, ns, cr = s.avg_logprob, s.no_speech_prob, s.compression_ratio
        suspect = is_suspect_phrase(s.text)
        bad = False
        if suspect and ((lp is not None and lp < -0.7) or (ns is not None and ns > 0.4)):
            bad = True
        if ns is not None and lp is not None and ns > 0.8 and lp < -1.0:
            bad = True
        if cr is not None and cr > 3.5:
            bad = True
        if strict and ((ns is not None and ns > 0.5) or (lp is not None and lp < -1.2) or suspect):
            bad = True
        (dropped if bad else kept).append(s)
    return kept, dropped


def collapse_repeats(segs: Sequence[Seg], max_repeat: int = 2) -> Tuple[List[Seg], int]:
    """Câu giống hệt nhau liên tiếp quá max_repeat lần thì bỏ phần thừa (lỗi lặp vòng của Whisper)."""
    out: List[Seg] = []
    run, last, dropped = 0, None, 0
    for s in segs:
        key = norm_text(s.text)
        if key and key == last:
            run += 1
            if run > max_repeat:
                dropped += 1
                continue
        else:
            run, last = 1, key
        out.append(s)
    return out, dropped


def sanitize(segs: Sequence[Seg], total: Optional[float] = None) -> List[Seg]:
    """Sắp xếp, ép trong [0,total], sửa câu có độ dài <= 0, chống chồng lấn, dọn dẹp mốc từ."""
    out = []
    for s in sorted(segs, key=lambda x: (x.start, x.end)):
        s.start = max(0.0, s.start)
        if total is not None:
            s.end = min(s.end, total)
        if s.end <= s.start:
            s.end = s.start + 0.3
        out.append(s)
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if a.end > b.start:
            a.end = max(a.start + 0.05, b.start)
    for s in out:
        prev = s.start
        for w in s.words:
            w.start = min(max(w.start, s.start), s.end)
            w.end = min(max(w.end, w.start), s.end)
            w.start = max(w.start, prev)
            w.end = max(w.end, w.start)
            prev = w.start
    return out


def merge_short(segs: Sequence[Seg], cfg: Config, lang: str) -> List[Seg]:
    """Gộp câu quá ngắn với câu kề bên khi khoảng nghỉ nhỏ và gộp xong vẫn trong giới hạn."""
    limit_chars = max_chars_for(cfg, lang)
    out: List[Seg] = []
    for s in segs:
        if out:
            p = out[-1]
            gap = s.start - p.end
            short = p.duration < cfg.min_seg_seconds or s.duration < cfg.min_seg_seconds
            joined = join_text(p.text, s.text, lang)
            if short and gap <= cfg.merge_gap and (s.end - p.start) <= cfg.max_seg_seconds \
                    and count_chars(joined) <= limit_chars:
                p.text, p.end = joined, s.end
                p.words = p.words + s.words
                p.flags = sorted(set(p.flags) | set(s.flags))
                for attr in ("avg_logprob", "no_speech_prob", "compression_ratio"):
                    a, b = getattr(p, attr), getattr(s, attr)
                    if a is None or b is None:
                        setattr(p, attr, a if b is None else b)
                    else:
                        setattr(p, attr, min(a, b) if attr == "avg_logprob" else max(a, b))
                continue
        out.append(s)
    return out


def _needs_split(s: Seg, cfg: Config, lang: str) -> bool:
    return s.duration > cfg.max_seg_seconds or count_chars(s.text) > max_chars_for(cfg, lang)


def _best_split_index(words: Sequence[Word], start: float, end: float) -> int:
    """Chọn vị trí cắt (cắt TRƯỚC words[i]) ưu tiên: gần giữa, sau dấu câu, sau khoảng nghỉ dài."""
    dur = max(end - start, 1e-6)
    best_i, best_score = len(words) // 2 or 1, -1e9
    for i in range(1, len(words)):
        left_end = words[i - 1].end
        gap = max(0.0, words[i].start - words[i - 1].end)
        tail = words[i - 1].word.rstrip()[-1:] if words[i - 1].word.strip() else ""
        punct = 1.0 if tail and tail in SENT_END else (0.5 if tail and tail in CLAUSE_END else 0.0)
        balance = 1.0 - abs((left_end - start) / dur - 0.5) * 2.0
        score = balance + 0.9 * punct + min(gap, 0.6) * 1.5
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def _split_by_text(s: Seg, lang: str) -> List[Seg]:
    """Không có mốc từ: cắt theo dấu câu gần giữa văn bản, chia thời gian theo tỉ lệ ký tự."""
    text = s.text
    marks = [m.end() for m in re.finditer(r"[%s%s]\s*" % (re.escape(SENT_END), re.escape(CLAUSE_END)), text)]
    marks = [m for m in marks if 0 < m < len(text)]
    cut = min(marks, key=lambda m: abs(m - len(text) / 2)) if marks else len(text) // 2
    left, right = text[:cut].strip(), text[cut:].strip()
    if not left or not right:
        return [s]
    ratio = len(left) / (len(left) + len(right))
    mid = s.start + s.duration * ratio
    return [Seg(s.start, mid, left, flags=list(s.flags), avg_logprob=s.avg_logprob,
                no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio),
            Seg(mid, s.end, right, flags=list(s.flags), avg_logprob=s.avg_logprob,
                no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio)]


def split_long(segs: Sequence[Seg], cfg: Config, lang: str, _depth: int = 0) -> List[Seg]:
    out: List[Seg] = []
    for s in segs:
        if not _needs_split(s, cfg, lang) or _depth > 6:
            out.append(s)
            continue
        if len(s.words) >= 2:
            i = _best_split_index(s.words, s.start, s.end)
            lw, rw = s.words[:i], s.words[i:]
            parts = [
                Seg(s.start, lw[-1].end, join_words(lw, lang), lw, flags=list(s.flags),
                    avg_logprob=s.avg_logprob, no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio),
                Seg(rw[0].start, s.end, join_words(rw, lang), rw, flags=list(s.flags),
                    avg_logprob=s.avg_logprob, no_speech_prob=s.no_speech_prob, compression_ratio=s.compression_ratio),
            ]
            if parts[0].end <= parts[0].start:
                parts[0].end = parts[0].start + 0.05
        else:
            parts = _split_by_text(s, lang)
            if len(parts) == 1:
                out.append(s)
                continue
        out.extend(split_long(parts, cfg, lang, _depth + 1))
    return out


def renumber(segs: Sequence[Seg]) -> List[Seg]:
    for i, s in enumerate(segs, 1):
        s.id = i
    return list(segs)


def postprocess(segs: Sequence[Seg], cfg: Config, lang: str, total: Optional[float] = None
                ) -> Tuple[List[Seg], dict]:
    """Chuỗi hậu xử lý đầy đủ, dùng chung cho lần quét chính và sau khi thêm câu quét lại.
    Trả về (danh sách câu, thống kê: số câu 'bịa' và số câu lặp đã bỏ)."""
    stats = {"dropped_hallucinations": 0, "dropped_repeats": 0}
    work = [s for s in segs if s.text.strip()]
    if cfg.drop_hallucinations:
        work, dropped = drop_hallucinations(work, cfg)
        stats["dropped_hallucinations"] = len(dropped)
        if dropped:
            log(f"🧹 Bỏ {len(dropped)} câu nghi là Whisper 'bịa' (vd: {dropped[0].text[:30]!r})")
    work, n_rep = collapse_repeats(sanitize(work, total))
    stats["dropped_repeats"] = n_rep
    if n_rep:
        log(f"🧹 Bỏ {n_rep} câu lặp lại liên tiếp")
    work = sanitize(work, total)
    work = merge_short(work, cfg, lang)
    work = split_long(work, cfg, lang)
    return renumber(sanitize(work, total)), stats


# ============================================================ khoảng trống & quét lại
def find_gaps(segs: Sequence[Seg], total: float, min_gap: float) -> List[Tuple[float, float]]:
    gaps, prev_end = [], 0.0
    for s in sorted(segs, key=lambda x: x.start):
        if s.start - prev_end >= min_gap:
            gaps.append((prev_end, s.start))
        prev_end = max(prev_end, s.end)
    if total - prev_end >= min_gap:
        gaps.append((prev_end, total))
    return gaps


def recheck_gaps(asr, audio_path, gaps: Sequence[Tuple[float, float]], cfg: Config, work_dir,
                 total: float) -> List[Seg]:
    """Quét lại từng khoảng trống dài với VAD TẮT để vớt lời nói nhỏ/bị VAD cắt nhầm."""
    recovered: List[Seg] = []
    wd = Path(work_dir)
    wd.mkdir(parents=True, exist_ok=True)
    pad = 0.25
    for k, (a, b) in enumerate(gaps, 1):
        lo, hi = max(0.0, a - pad), min(total, b + pad)
        clip = cut_audio(audio_path, wd / f"gap_{k:03d}.wav", lo, hi)
        found = asr.transcribe(clip, offset=lo, vad=False)
        inside = [s for s in found if a <= (s.start + s.end) / 2 <= b]
        good, _ = drop_hallucinations(inside, cfg, strict=True)
        for s in good:
            s.flags = sorted(set(s.flags) | {"recovered"})
        recovered.extend(good)
        log(f"   khoảng trống {fmt_bracket(a)} -> {fmt_bracket(b)}: tìm lại {len(good)} câu")
    return recovered


# ============================================================ QC
def annotate_flags(segs: Sequence[Seg], cfg: Config, lang: str) -> None:
    limit = FAST_LIMIT_SRC.get(lang, FAST_LIMIT_DEFAULT)
    for s in segs:
        f = set(s.flags)
        if s.avg_logprob is not None and s.avg_logprob < cfg.low_logprob:
            f.add("low_conf")
        if s.no_speech_prob is not None and s.no_speech_prob > cfg.no_speech_flag:
            f.add("no_speech")
        if s.compression_ratio is not None and s.compression_ratio > cfg.repetition_ratio:
            f.add("repetitive")
        if s.duration > 0 and count_chars(s.text) / s.duration > limit:
            f.add("fast_speech")
        if s.duration > cfg.max_seg_seconds * 1.5:
            f.add("too_long")
        if s.duration < 0.3:
            f.add("too_short")
        if is_suspect_phrase(s.text):
            f.add("suspect_phrase")
        s.flags = sorted(f)


def build_qc(segs: Sequence[Seg], total: float, cfg: Config, lang: str, info: Optional[dict] = None,
             dropped: int = 0) -> dict:
    segs = list(segs)
    speech = sum(s.duration for s in segs)
    counts: Dict[str, int] = {}
    for s in segs:
        for f in s.flags:
            counts[f] = counts.get(f, 0) + 1
    gaps = find_gaps(segs, total, cfg.gap_warn)
    issues = [{"id": s.id, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text,
               "flags": s.flags} for s in segs if set(s.flags) - {"recovered"}]
    return {
        "video_duration": round(total, 2),
        "language": lang,
        "language_probability": (info or {}).get("language_probability"),
        "segments": len(segs),
        "speech_seconds": round(speech, 1),
        "coverage": round(speech / total, 3) if total else 0.0,
        "avg_segment_seconds": round(speech / len(segs), 2) if segs else 0.0,
        "max_segment_seconds": round(max((s.duration for s in segs), default=0.0), 2),
        "flag_counts": counts,
        "long_gaps": [{"start": round(a, 2), "end": round(b, 2), "seconds": round(b - a, 1)} for a, b in gaps],
        "dropped_hallucinations": dropped,
        "issues": issues,
    }


FLAG_HELP = {
    "low_conf": "độ tin cậy thấp, nên nghe lại",
    "no_speech": "có thể không phải lời nói (nhạc/tiếng động)",
    "repetitive": "văn bản lặp, nghi lỗi nhận diện",
    "fast_speech": "nói rất nhanh, khi dịch dễ bị quá ngắn khung",
    "too_long": "câu quá dài",
    "too_short": "câu quá ngắn",
    "suspect_phrase": "giống câu Whisper hay bịa",
    "recovered": "câu tìm lại ở lần quét khoảng trống",
}


def qc_markdown(qc: dict, max_rows: int = 40) -> str:
    L = ["# Báo cáo QC", "",
         f"- Độ dài video: **{qc['video_duration']}s**, ngôn ngữ: **{qc['language']}**",
         f"- Số câu: **{qc['segments']}**, thời lượng có lời: **{qc['speech_seconds']}s** "
         f"({qc['coverage']:.0%} video)",
         f"- Câu trung bình: {qc['avg_segment_seconds']}s, dài nhất: {qc['max_segment_seconds']}s",
         f"- Câu 'bịa' đã bỏ: {qc['dropped_hallucinations']}", ""]
    if qc["flag_counts"]:
        L += ["## Có cảnh báo", ""] + [f"- `{k}`: {v} câu ({FLAG_HELP.get(k, '')})"
                                      for k, v in sorted(qc["flag_counts"].items())] + [""]
    if qc["long_gaps"]:
        L += ["## Khoảng trống dài (có thể bị bỏ sót lời hoặc chỉ là nhạc)", ""] + \
             [f"- {fmt_bracket(g['start'])} -> {fmt_bracket(g['end'])} ({g['seconds']}s)" for g in qc["long_gaps"]] + [""]
    if qc["issues"]:
        L += [f"## Câu cần kiểm tra (tối đa {max_rows})", "", "| # | Thời gian | Cờ | Nội dung |", "|---|---|---|---|"]
        for it in qc["issues"][:max_rows]:
            L.append(f"| {it['id']} | {it['start']}-{it['end']}s | {', '.join(it['flags'])} | "
                     f"{it['text'][:60].replace('|', '/')} |")
        if len(qc["issues"]) > max_rows:
            L.append(f"\n... và {len(qc['issues']) - max_rows} câu khác (xem qc_report.json)")
    return "\n".join(L) + "\n"


# ============================================================ xuất file
def to_dict(segs: Sequence[Seg], source: dict, cfg: Optional[Config] = None) -> dict:
    out = {"schema": SCHEMA, "generator": f"dubkaggle {__version__}", "source": source,
           "settings": {k: v for k, v in (asdict(cfg).items() if cfg else [])
                        if k not in ("video_path", "work_dir", "out_dir")},
           "segments": []}
    for s in segs:
        d = {"id": s.id, "start": round(s.start, 3), "end": round(s.end, 3), "text": s.text}
        if s.src:
            d["src"] = s.src
        d["words"] = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3),
                       **({"prob": round(w.prob, 3)} if w.prob is not None else {})} for w in s.words]
        d["flags"] = s.flags
        for k in ("avg_logprob", "no_speech_prob", "compression_ratio"):
            v = getattr(s, k)
            if v is not None:
                d[k] = round(v, 4)
        out["segments"].append(d)
    return out


def load_transcript(path) -> Tuple[List[Seg], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"Sai định dạng transcript (cần schema {SCHEMA}, nhận {data.get('schema')!r})")
    segs = []
    for d in data["segments"]:
        words = [Word(w["word"], w["start"], w["end"], w.get("prob")) for w in d.get("words", [])]
        segs.append(Seg(d["start"], d["end"], d["text"], words, d.get("id", 0), d.get("src", ""),
                        d.get("avg_logprob"), d.get("no_speech_prob"), d.get("compression_ratio"),
                        list(d.get("flags", []))))
    return segs, data.get("source", {})


def write_json(path, obj) -> Path:
    p = Path(path)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def format_srt(segs: Sequence[Seg]) -> str:
    return "\n".join(f"{i}\n{fmt_srt(s.start)} --> {fmt_srt(s.end)}\n{s.text.strip()}\n"
                     for i, s in enumerate(segs, 1))


def format_script(segs: Sequence[Seg]) -> str:
    """Định dạng '[HH:MM:SS.mmm -> HH:MM:SS.mmm] nội dung' (DubStudio đọc trực tiếp được)."""
    return "\n".join(f"[{fmt_bracket(s.start)} -> {fmt_bracket(s.end)}] {s.text.strip()}" for s in segs) + "\n"


def make_bundle(out_dir, bundle_path) -> Path:
    out_dir, bundle_path = Path(out_dir), Path(bundle_path)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(out_dir))
    return bundle_path


def find_videos(roots: Sequence[str] = ("/kaggle/input", ".")) -> List[str]:
    found: List[str] = []
    for r in roots:
        if os.path.isdir(r):
            for dirpath, _, files in os.walk(r):
                found += [os.path.join(dirpath, f) for f in files if f.lower().endswith(VIDEO_EXTS)]
    return sorted(found)


# ============================================================ pipeline giai đoạn 1
def run_transcribe(cfg: Config, asr=None) -> dict:
    """Chạy trọn giai đoạn 1. `asr` có thể thay bằng đối tượng giả khi test."""
    video = Path(cfg.video_path)
    if not video.is_file():
        raise FileNotFoundError(f"Không thấy video: {video}")
    work, out = Path(cfg.work_dir), Path(cfg.out_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    total = probe_duration(video)
    if not has_audio_stream(video):
        raise RuntimeError("Video không có luồng âm thanh để nhận diện")
    log(f"🎬 {video.name}: {total:.1f}s")
    audio = extract_audio(video, work / "audio_16k.wav", 16000, cfg.normalize_audio)
    if cfg.separate_vocals:
        log("🎼 Tách giọng khỏi nhạc nền (Demucs)...")
        vocals = separate_vocals(audio, work)
        if vocals:
            audio = vocals

    asr = asr or WhisperASR(cfg)
    log("🎧 Nhận diện giọng nói (có mốc từng từ)...")
    raw = asr.transcribe(audio)
    info = getattr(asr, "info", {}) or {}
    lang = cfg.lang_code(info.get("language"))
    log(f"✓ Whisper trả về {len(raw)} đoạn thô")

    segs, stats = postprocess(raw, cfg, lang, total)
    if cfg.recheck_gaps:
        gaps = [g for g in find_gaps(segs, total, cfg.gap_recheck_min)]
        if gaps:
            log(f"🔍 Quét lại {len(gaps)} khoảng trống dài >= {cfg.gap_recheck_min}s (tắt VAD)...")
            recovered = recheck_gaps(asr, audio, gaps, cfg, work, total)
            if recovered:
                segs, stats2 = postprocess(list(segs) + recovered, cfg, lang, total)
                stats["dropped_hallucinations"] += stats2["dropped_hallucinations"]
                log(f"✓ Tìm lại được {len(recovered)} câu bị bỏ sót")

    annotate_flags(segs, cfg, lang)
    qc = build_qc(segs, total, cfg, lang, info, dropped=stats["dropped_hallucinations"])
    source = {"file": video.name, "duration": round(total, 2), "language": lang,
              "language_probability": info.get("language_probability")}
    paths = {
        "transcript_json": write_json(out / "transcript.json", to_dict(segs, source, cfg)),
        "srt": out / "transcript.srt",
        "script": out / "script.txt",
        "qc_json": write_json(out / "qc_report.json", qc),
        "qc_md": out / "qc_report.md",
    }
    paths["srt"].write_text(format_srt(segs), encoding="utf-8")
    paths["script"].write_text(format_script(segs), encoding="utf-8")
    paths["qc_md"].write_text(qc_markdown(qc), encoding="utf-8")
    paths["bundle"] = make_bundle(out, out.parent / "dubstudio_output.zip")
    log(f"✅ Xong sau {time.time() - t0:.0f}s: {len(segs)} câu, phủ {qc['coverage']:.0%} video")
    return {"segments": segs, "qc": qc, "paths": paths, "source": source}


# ============================================================ giai đoạn 2: dịch + kiểm tra tốc độ đọc
@dataclass
class TranslateConfig:
    source_lang: str = "zh"
    target_lang: str = "vi"
    model: str = "gemini-3.5-flash"
    fallback_models: Tuple[str, ...] = ("gemini-3.1-flash-lite", "gemini-flash-latest")
    topic: str = ""
    style: str = "Tự nhiên, dễ nghe, đúng văn nói"
    glossary: str = ""                 # "Tên gốc=Tên dịch", các mục cách nhau dấu phẩy
    pronoun_rules: str = ""            # vd "Nobita gọi Doraemon là 'Doraemon'; Doraemon xưng 'tôi'"
    batch_size: int = 40
    cps: float = 0.0                   # 0 = dùng TARGET_CPS; hãy đo bằng calibrate_cps()
    speed_cap: float = 1.25            # tốc độ tăng tối đa chấp nhận được cho giọng đọc
    shorten_rounds: int = 2
    out_dir: str = "/kaggle/working/output_translate"


GenerateFn = Callable[[str, str, str], str]  # (system, user, model) -> text


def target_cps(tcfg: TranslateConfig) -> float:
    return tcfg.cps or TARGET_CPS.get(tcfg.target_lang, 14.0)


def fit_window(segs: Sequence[Seg], i: int, total: float) -> float:
    """Thời gian câu i được phép chiếm: đến lúc câu kế tiếp bắt đầu (cho mượn khoảng lặng)."""
    s = segs[i]
    nxt = segs[i + 1].start if i + 1 < len(segs) else total
    return max(s.duration, nxt - s.start - 0.05)


def fit_rows(segs: Sequence[Seg], total: float, cps: float, cap: float) -> List[dict]:
    rows = []
    for i, s in enumerate(segs):
        window = fit_window(segs, i, total)
        est = speech_len(s.text) / cps
        need = est / window if window > 0 else 99.0
        status = "ok" if need <= 1.0 else ("tight" if need <= cap else "too_fast")
        rows.append({"id": s.id or i + 1, "index": i, "chars": speech_len(s.text), "window": round(window, 2),
                     "est_seconds": round(est, 2), "need_speed": round(need, 2), "status": status,
                     "max_chars": max(4, int(window * cps * cap * 0.95))})
    return rows


SLOWDOWN_MAX_RECOMMENDED = 1.2   # làm chậm cả video quá mức này thì chuyển động bị kéo rõ rệt


def suggest_video_slowdown(rows: Sequence[dict], cap: float, percentile: float = 0.9) -> float:
    """Hệ số làm chậm video chung (>=1.0) để phần lớn câu vừa mà không cần đọc nhanh hơn `cap`.
    Chỉ là gợi ý: làm chậm cả video thì chuyển động cũng chậm theo."""
    need = sorted(r["need_speed"] / cap for r in rows)
    if not need:
        return 1.0
    k = min(len(need) - 1, max(0, int(math.ceil(percentile * len(need))) - 1))
    return round(max(1.0, need[k]), 2)


def fit_summary(rows: Sequence[dict], cap: float) -> dict:
    n = len(rows)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("ok", "tight", "too_fast")}
    extra = sum(max(0.0, r["est_seconds"] / cap - r["window"]) for r in rows)
    slow = suggest_video_slowdown(rows, cap)
    return {"lines": n, **counts, "extra_seconds_needed": round(extra, 1),
            "suggested_video_slowdown": slow, "slowdown_recommended": 1.0 < slow <= SLOWDOWN_MAX_RECOMMENDED}


def _slowdown_line(summary: dict) -> str:
    slow = summary["suggested_video_slowdown"]
    if slow <= 1.0:
        return "- Không cần làm chậm video."
    if summary["slowdown_recommended"]:
        return (f"- Gợi ý làm chậm video chung: x{slow} (đủ để từ 90% câu trở lên vừa khung "
                f"mà không phải đọc nhanh hơn mức tối đa).")
    return (f"- Muốn từ 90% câu vừa khung chỉ bằng cách làm chậm cả video thì cần x{slow}: "
            f"**không khuyến nghị** (chuyển động bị kéo rõ rệt). Hãy rút gọn hoặc sửa tay các câu quá nhanh.")


def fit_markdown(rows: Sequence[dict], summary: dict, segs: Sequence[Seg], cap: float, max_rows: int = 40) -> str:
    L = ["# Báo cáo tốc độ đọc", "",
         f"- Tổng câu: **{summary['lines']}**  |  ok: {summary['ok']}  |  sát (<= {cap}x): {summary['tight']}  "
         f"|  **quá nhanh (> {cap}x): {summary['too_fast']}**",
         f"- Thời gian còn thiếu nếu giữ tốc độ tối đa {cap}x: ~{summary['extra_seconds_needed']}s",
         _slowdown_line(summary),
         "", "Lưu ý: đây là ƯỚC LƯỢNG dựa trên tốc độ đọc giả định. Hãy đo bằng calibrate_cps() để sát thực tế.", ""]
    bad = [r for r in rows if r["status"] == "too_fast"]
    if bad:
        L += [f"## Câu quá nhanh (tối đa {max_rows})", "", "| # | Khung | Cần tốc độ | Tối đa ký tự | Nội dung |", "|---|---|---|---|---|"]
        for r in bad[:max_rows]:
            L.append(f"| {r['id']} | {r['window']}s | {r['need_speed']}x | {r['max_chars']} | "
                     f"{segs[r['index']].text[:50].replace('|', '/')} |")
    return "\n".join(L) + "\n"


def calibrate_cps(samples: Sequence[str], synth_fn: Callable[[str], float]) -> float:
    """Đo tốc độ đọc thực tế: synth_fn(text) -> số giây audio. Trả về ký tự/giây (có khoảng trắng)."""
    chars = sum(speech_len(t) for t in samples)
    secs = sum(synth_fn(t) for t in samples)
    if secs <= 0:
        raise ValueError("Tổng thời lượng audio bằng 0")
    return round(chars / secs, 2)


def _system_prompt(src: str, tgt: str) -> str:
    return (
        f"You are a professional subtitle translator and dubbing-script adapter. Translate the given lines "
        f"from {src} to {tgt}. The result is read aloud by a text-to-speech voice and MUST fit the time slot.\n"
        "Rules:\n"
        "1. Output ONLY a JSON array [{\"id\": <int>, \"text\": <string>}, ...] with the same ids, same count, "
        "same order. Never merge, split, drop or add lines.\n"
        "2. Write natural SPOKEN language, not literal translation. Fix obvious speech-recognition errors.\n"
        "3. Each line has `max_chars`: the translation MUST NOT exceed it (spaces count). Shorten by removing "
        "filler and repetition, never by dropping key meaning.\n"
        "4. Keep names, terminology and forms of address consistent across ALL lines. Follow the glossary "
        "exactly and apply `pronoun_rules` to every line involving those speakers.\n"
        "5. Write numbers and symbols the way they are spoken. `previous_lines` is context only."
    )


class Translator:
    def __init__(self, tcfg: TranslateConfig, generate_fn: GenerateFn,
                 sleep: Callable[[float], None] = time.sleep, logger: Callable[[str], None] = log):
        self.t, self.generate, self.sleep, self.log = tcfg, generate_fn, sleep, logger
        self.model = tcfg.model
        self._dead: set = set()

    # --- goi model, tu doi model khi 403/404, thu lai khi 429/5xx
    def _chain(self) -> List[str]:
        chain = [self.model] + [m for m in self.t.fallback_models if m != self.model]
        return [m for m in chain if m not in self._dead] or chain

    def _call(self, system: str, user: str) -> str:
        last: Optional[Exception] = None
        for model in self._chain():
            for attempt in range(4):
                try:
                    out = self.generate(system, user, model)
                    if model != self.model:
                        self.log(f"ℹ️ Dùng model dự phòng: {model}")
                        self.model = model
                    return out
                except Exception as e:  # noqa: BLE001
                    last, code, msg = e, getattr(e, "code", None), str(e).lower()
                    if code in (403, 404) or any(k in msg for k in ("not found", "no longer available",
                                                                     "not supported", "permission", "forbidden")):
                        self.log(f"⚠️ Model '{model}' không dùng được với key này, thử model khác...")
                        self._dead.add(model)
                        break
                    if code in (429, 500, 502, 503, 504) or any(k in msg for k in ("quota", "overloaded", "unavailable")):
                        wait = 2 ** (attempt + 1)
                        self.log(f"⏳ API bận, chờ {wait}s...")
                        self.sleep(wait)
                        continue
                    raise
        raise RuntimeError(f"Gọi Gemini thất bại sau nhiều lần thử: {last}")

    @staticmethod
    def _parse(text: str) -> Dict[int, str]:
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S).strip()
        try:
            data = json.loads(t)
        except ValueError:
            m = re.search(r"\[.*\]", t, re.S)
            if not m:
                return {}
            data = json.loads(m.group(0))
        if isinstance(data, dict):
            data = next((data[k] for k in ("translations", "lines", "items", "result") if isinstance(data.get(k), list)), [])
        out: Dict[int, str] = {}
        for d in data if isinstance(data, list) else []:
            if isinstance(d, dict) and "id" in d and "text" in d:
                try:
                    out[int(d["id"])] = str(d["text"]).strip()
                except (TypeError, ValueError):
                    pass
        return out

    def _ask(self, system: str, lines: List[dict], prev: List[str]) -> Dict[int, str]:
        payload = {"topic": self.t.topic or "(not specified)", "style": self.t.style or "natural spoken language",
                   "glossary": self.t.glossary or "(none)",
                   "pronoun_rules": self.t.pronoun_rules or "(not specified: use natural pronouns)",
                   "previous_lines": prev, "lines": lines}
        try:
            got = self._parse(self._call(system, json.dumps(payload, ensure_ascii=False)))
        except (RuntimeError, ValueError) as e:
            self.log(f"⚠️ Lỗi một đợt: {e}")
            return {}
        ids = {ln["id"] for ln in lines}
        return {i: t for i, t in got.items() if i in ids and t}

    def translate(self, segs: Sequence[Seg], total: float) -> List[Seg]:
        t = self.t
        cps = target_cps(t)
        system = _system_prompt(LANG_NAMES.get(t.source_lang, t.source_lang), LANG_NAMES.get(t.target_lang, t.target_lang))
        out = [Seg(s.start, s.end, "", [], s.id, s.src or s.text) for s in segs]
        prev: List[str] = []
        n = len(segs)
        for b0 in range(0, n, t.batch_size):
            idx = list(range(b0, min(n, b0 + t.batch_size)))

            def lines_for(ix):
                return [{"id": i, "text": segs[i].src or segs[i].text,
                         "max_chars": max(6, int(fit_window(segs, i, total) * cps * t.speed_cap * 0.95))}
                        for i in ix]

            got = self._ask(system, lines_for(idx), prev)
            missing = [i for i in idx if i not in got]
            if missing:
                self.log(f"⚠️ Thiếu {len(missing)} câu, dịch lại riêng...")
                got.update(self._ask(system, lines_for(missing), prev))
            for i in idx:
                out[i].text = got.get(i) or segs[i].src or segs[i].text   # thất bại: giữ nguyên bản gốc
                if i not in got:
                    out[i].flags = sorted(set(out[i].flags) | {"untranslated"})
            prev = [out[i].text for i in idx[-4:]]
            self.log(f"   đã dịch {min(idx[-1] + 1, n)}/{n} câu")
        return out

    def shorten(self, segs: List[Seg], total: float) -> Tuple[List[Seg], List[dict]]:
        """Rút gọn các câu quá nhanh. Chỉ nhận bản rút gọn nếu NGẮN HƠN bản cũ và không rỗng."""
        t = self.t
        cps = target_cps(t)
        system = (f"You shorten {LANG_NAMES.get(t.target_lang, t.target_lang)} dubbing lines so a TTS voice can read "
                  "them in time. Output ONLY a JSON array [{\"id\": <int>, \"text\": <string>}, ...]. Keep the meaning, "
                  "names and tone; drop filler and repetition. Each line MUST be at most `max_chars` characters "
                  "(spaces count) and strictly shorter than `current`.")
        rows = fit_rows(segs, total, cps, t.speed_cap)
        for rnd in range(1, t.shorten_rounds + 1):
            bad = [r for r in rows if r["status"] == "too_fast"]
            if not bad:
                break
            self.log(f"✂️ Vòng rút gọn {rnd}: {len(bad)} câu quá nhanh")
            for b0 in range(0, len(bad), t.batch_size):
                chunk = bad[b0:b0 + t.batch_size]
                lines = [{"id": r["index"], "current": segs[r["index"]].text, "max_chars": r["max_chars"]} for r in chunk]
                got = self._ask(system, lines, [])
                for r in chunk:
                    new = got.get(r["index"])
                    if new and speech_len(new) < speech_len(segs[r["index"]].text):
                        segs[r["index"]].text = new
                        segs[r["index"]].flags = sorted(set(segs[r["index"]].flags) | {"shortened"})
            rows = fit_rows(segs, total, cps, t.speed_cap)
        return segs, rows


def make_gemini_generate(api_key: str) -> GenerateFn:
    """Bọc google-genai thành generate_fn. Import trễ để máy không có thư viện vẫn import được module."""
    from google import genai
    from google.genai import types

    if not api_key:
        raise RuntimeError("Chưa có GEMINI_API_KEY (thêm vào Kaggle Secrets, tên GEMINI_API_KEY)")
    client = genai.Client(api_key=api_key)

    def generate(system: str, user: str, model: str) -> str:
        resp = client.models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               temperature=0.3))
        if not resp.text:
            raise RuntimeError("Model trả về rỗng (có thể bị bộ lọc an toàn chặn)")
        return resp.text

    return generate


def list_gemini_models(api_key: str) -> List[str]:
    from google import genai

    names = []
    for m in genai.Client(api_key=api_key).models.list():
        actions = getattr(m, "supported_actions", None) or []
        name = (m.name or "").replace("models/", "")
        if "generateContent" in actions and name.startswith("gemini") and not re.search(
                r"image|tts|live|audio|embed|robotics|computer|transcribe", name):
            names.append(name)
    return sorted(set(names), reverse=True)


def get_secret(name: str) -> str:
    """Lấy key từ Kaggle Secrets, nếu không có thì từ biến môi trường."""
    try:
        from kaggle_secrets import UserSecretsClient  # type: ignore

        return UserSecretsClient().get_secret(name)
    except Exception:  # noqa: BLE001
        return os.environ.get(name, "")


def run_translate(transcript_path, tcfg: TranslateConfig, generate_fn: GenerateFn,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    segs, source = load_transcript(transcript_path)
    total = float(source.get("duration") or (segs[-1].end if segs else 0.0))
    out = Path(tcfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tr = Translator(tcfg, generate_fn, sleep=sleep)
    log(f"🌐 Dịch {len(segs)} câu: {tcfg.source_lang} -> {tcfg.target_lang} (model {tr.model})")
    translated = tr.translate(segs, total)

    cps = target_cps(tcfg)
    rows = fit_rows(translated, total, cps, tcfg.speed_cap)
    before = fit_summary(rows, tcfg.speed_cap)
    log(f"⏱️ Trước rút gọn: {before['too_fast']} câu quá nhanh / {before['lines']}")
    translated, rows = tr.shorten(translated, total)
    after = fit_summary(rows, tcfg.speed_cap)
    log(f"⏱️ Sau rút gọn: {after['too_fast']} câu quá nhanh / {after['lines']}")

    lang = tcfg.target_lang
    src_info = dict(source, language=lang, translated_from=tcfg.source_lang)
    paths = {
        "script": out / f"script_{lang}.txt",
        "srt": out / f"transcript_{lang}.srt",
        "json": write_json(out / f"transcript_{lang}.json", to_dict(translated, src_info)),
        "fit_json": write_json(out / "fit_report.json", {"before": before, "after": after, "rows": rows}),
        "fit_md": out / "fit_report.md",
    }
    paths["script"].write_text(format_script(translated), encoding="utf-8")
    paths["srt"].write_text(format_srt(translated), encoding="utf-8")
    paths["fit_md"].write_text(fit_markdown(rows, after, translated, tcfg.speed_cap), encoding="utf-8")
    paths["bundle"] = make_bundle(out, out.parent / f"dubstudio_translate_{lang}.zip")
    return {"segments": translated, "before": before, "after": after, "rows": rows, "paths": paths}
