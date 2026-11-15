"""Điều phối toàn bộ quy trình: tải → nhận diện → dịch → tạo giọng → ghép → trộn → xuất.

GUI và CLI đều gọi vào đây; module này không biết gì về giao diện.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import subtitles as sub
from .audio_track import build_track, render_clips
from .cache import AudioCache
from .config import DOWNLOAD_DIR, OUTPUT_DIR, WORK_ROOT, DubSettings, Keys
from .downloader import download_video, is_url
from .errors import DubberError, MissingDependency
from .ffmpeg_utils import probe
from .mixer import mix_video
from .reporter import Reporter
from .subtitle_engine import generate_karaoke_ass
from .subtitles import Segment
from .transcriber import transcribe
from .translator import FreeTranslator, GeminiTranslator
from .tts import TTSEngine, make_engine


class DubbingPipeline:
    def __init__(self, settings: DubSettings, keys: Optional[Keys] = None, reporter: Optional[Reporter] = None,
                 *, tts_engine: Optional[TTSEngine] = None, translator=None, work_root: Optional[Path] = None,
                 cache: Optional[AudioCache] = None):
        self.s = settings
        self.keys = keys or Keys.from_env()
        self.rep = reporter or Reporter()
        self._engine = tts_engine
        self._translator = translator
        self.work_root = Path(work_root) if work_root else WORK_ROOT
        self.cache = cache or AudioCache()
        self.detected_lang: Optional[str] = None
        self._workdir: Optional[Path] = None

    # ------------------------------------------------------------------ tiện ích
    def workdir(self) -> Path:
        if self._workdir is None:
            self._workdir = self.work_root / time.strftime("job_%Y%m%d_%H%M%S")
            self._workdir.mkdir(parents=True, exist_ok=True)
        return self._workdir

    def cleanup(self) -> None:
        if self._workdir and not self.s.keep_temp:
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workdir = None

    def default_output(self, video: Path) -> Path:
        return OUTPUT_DIR / f"{Path(video).stem}_dub_{self.s.target_lang}.mp4"

    # --------------------------------------------------------------- các bước lẻ
    def acquire_video(self, source: str, rep: Optional[Reporter] = None) -> Path:
        rep = rep or self.rep
        source = str(source).strip().strip('"')
        if is_url(source):
            return download_video(source, DOWNLOAD_DIR, rep)
        p = Path(source)
        if not p.is_file():
            raise DubberError(f"Không tìm thấy file video: {source}")
        return p

    def transcribe(self, video: Path, rep: Optional[Reporter] = None) -> List[Segment]:
        rep = rep or self.rep
        segs, lang = transcribe(
            video, self.workdir(), model_size=self.s.whisper_model,
            language=None if self.s.source_lang == "auto" else self.s.source_lang,
            max_segment_seconds=self.s.max_segment_seconds, reporter=rep)
        self.detected_lang = lang
        return segs

    def translate(self, segments: List[Segment], rep: Optional[Reporter] = None) -> List[Segment]:
        rep = rep or self.rep
        src = self.detected_lang or self.s.source_lang
        if src not in ("", "auto") and src.split("-")[0] == self.s.target_lang:
            rep.log("ℹ️ Ngôn ngữ gốc trùng ngôn ngữ đích → bỏ qua bước dịch")
            return [Segment(s.start, s.end, s.text, s.src or s.text) for s in segments]
        tr = self._translator
        if tr is None:
            if self.keys.gemini:
                tr = GeminiTranslator(self.keys.gemini, self.s.gemini_model)
            else:
                rep.log("⚠️ Chưa có GEMINI_API_KEY → dùng Google Translate miễn phí (kém hiểu ngữ cảnh hơn)", "warn")
                tr = FreeTranslator()
        return tr.translate(segments, src or "auto", self.s.target_lang, topic=self.s.topic,
                            style=self.s.style, glossary=self.s.glossary,
                            pronoun_rules=self.s.pronoun_rules, reporter=rep)

    def prepare_script(self, source: str) -> Tuple[Path, List[Segment]]:
        """Bước 1: tải (nếu là link) → nhận diện giọng → dịch. Trả về (video, kịch bản) để người dùng chỉnh sửa."""
        video = self.acquire_video(source, self.rep.scope(0, 20))
        self.rep.check_cancel()
        segs = self.transcribe(video, self.rep.scope(20, 65))
        self.rep.check_cancel()
        segs = self.translate(segs, self.rep.scope(65, 100))
        self.rep.progress(100, "Kịch bản đã sẵn sàng, hãy kiểm tra rồi bấm Lồng tiếng")
        return video, segs

    def render(self, video, segments: List[Segment], output=None) -> Path:
        """Bước 2: tạo giọng → khớp thời gian → trộn → xuất MP4."""
        video = Path(video)
        segments = [s for s in sub.normalize(segments) if s.text.strip()]
        if not segments:
            raise DubberError("Kịch bản rỗng hoặc sai định dạng")
        output = Path(output) if output else self.default_output(video)
        info = probe(video)
        work = self.workdir()
        self.rep.log(f"🎬 Bắt đầu lồng tiếng {len(segments)} câu cho video dài {info.duration:.1f}s")

        engine = self._engine or make_engine(
            self.s, self.keys,
            on_fallback=lambda name, e: self.rep.log(f"⚠️ {name} lỗi ({e}) → chuyển engine dự phòng", "warn"))

        clips = render_clips(segments, engine, work, max_speed=self.s.max_speed, total_duration=info.duration,
                             workers=self.s.tts_workers, reporter=self.rep.scope(0, 70), cache=self.cache)
        self.rep.check_cancel()
        track = build_track(segments, clips, info.duration, work / "ai_track.wav")
        self.rep.progress(72, "Đã ghép track giọng AI")

        mode, bg_stem = self.s.mix_mode, None
        if mode == "stems":
            try:
                from .separator import separate_background
                bg_stem = separate_background(video, work, self.rep.scope(72, 85))
            except (MissingDependency, DubberError) as e:
                self.rep.log(f"⚠️ Không tách được nhạc nền ({e}) → chuyển sang chế độ 'duck'", "warn")
                mode = "duck"
        self.rep.check_cancel()

        srt_for_burn = None
        ass_for_burn = None
        has_word_timing = any(len(s.words) > 0 for s in segments)

        if self.s.burn_captions:
            if has_word_timing:
                ass_for_burn = work / "captions.ass"
                generate_karaoke_ass(segments, ass_for_burn, font_size=self.s.caption_size)
            else:
                srt_for_burn = work / "captions.srt"
                srt_for_burn.write_text(sub.format_srt(segments), encoding="utf-8")

        if self.s.export_srt:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.with_suffix(".srt").write_text(sub.format_srt(segments), encoding="utf-8")
            if has_word_timing:
                generate_karaoke_ass(segments, output.with_suffix(".ass"), font_size=self.s.caption_size)

        self.rep.progress(86, "Đang trộn âm thanh và xuất video...")
        mix_video(video, track, output, mode=mode, original_volume=self.s.original_volume,
                  ai_volume=self.s.ai_volume, bg_stem=bg_stem, bg_volume=self.s.bg_volume,
                  srt_path=srt_for_burn, ass_path=ass_for_burn, caption_size=self.s.caption_size,
                  reporter=self.rep)
        self.rep.progress(100, "Hoàn tất!")
        return output

    def run_all(self, source: str, output=None) -> Path:
        """Tự động toàn bộ từ link/file đến video thành phẩm."""
        try:
            sub_rep = self.rep
            self.rep = sub_rep.scope(0, 50)
            video, segs = self.prepare_script(source)
            work = self.workdir()
            (work / "script.txt").write_text(sub.format_editor(segs), encoding="utf-8")
            self.rep = sub_rep.scope(50, 100)
            out = self.render(video, segs, output)
            if self.s.export_srt or self.s.keep_temp:
                out.with_suffix(".script.txt").write_text(sub.format_editor(segs), encoding="utf-8")
            self.rep = sub_rep
            return out
        finally:
            self.cleanup()
