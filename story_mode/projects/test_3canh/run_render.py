import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DubSettings, Keys
from core.reporter import Reporter
from core.story_pipeline import StoryPipeline
from core.story_schema import load_storyboard
from core.tts import EdgeTTS

scene_timings = []

class TrackingEdgeTTS(EdgeTTS):
    def synthesize(self, text, out_path):
        words = super().synthesize(text, out_path)
        source = getattr(self, "last_timing_source", "unknown")
        scene_timings.append({
            "text": text,
            "timing_source": source,
            "words_count": len(words) if words else 0,
            "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (words or [])]
        })
        return words

def main():
    def log_cb(msg, lvl):
        print(f"[{lvl.upper()}] {msg}", flush=True)

    def prog_cb(pct, msg):
        print(f"[{pct:5.1f}%] {msg or ''}", flush=True)

    reporter = Reporter(on_log=log_cb, on_progress=prog_cb)
    settings = DubSettings()
    keys = Keys()

    engine = TrackingEdgeTTS(voice="vi-VN-HoaiMyNeural")
    pipeline = StoryPipeline(
        settings=settings,
        keys=keys,
        reporter=reporter,
        tts_engine=engine,
    )

    project_dir = Path("story_mode/projects/test_3canh")
    sb_path = project_dir / "storyboard.json"
    assets_dir = project_dir / "assets"

    out_mp4 = pipeline.render_story(
        storyboard_path_or_obj=sb_path,
        assets_dir=assets_dir,
        burn_subtitles=True,
        codec="h264",
    )

    print(f"\nOUTPUT_MP4: {out_mp4}")

    # Write timeline.json
    sb = load_storyboard(sb_path)
    timeline_data = []
    for sc, info in zip(sb.scenes, scene_timings):
        timeline_data.append({
            "id": sc.id,
            "speech": sc.speech,
            "timing_source": info["timing_source"],
            "words_count": info["words_count"],
            "words": info["words"],
        })

    timeline_path = out_mp4.with_suffix(".timeline.json")
    timeline_path.write_text(json.dumps(timeline_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TIMELINE_JSON: {timeline_path}")

    # In ra màn hình bảng tóm tắt timing_source
    print("\n--- TÓM TẮT MỐC THỜI GIAN TỪNG CẢNH ---")
    for t in timeline_data:
        print(f"Cảnh {t['id']}: timing_source={t['timing_source']}, số từ={t['words_count']}")

if __name__ == "__main__":
    main()
