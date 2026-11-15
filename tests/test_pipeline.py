from core.config import DubSettings, Keys
from core.ffmpeg_utils import probe
from core.pipeline import DubbingPipeline
from core.reporter import Reporter
from core.subtitles import Segment
from core.tts import ToneTTS

SCRIPT = [
    Segment(0.5, 3.0, "Xin chào các bạn."),
    Segment(3.5, 4.5, "Câu này dài hơn khung thời gian được cho phép rất nhiều."),
    Segment(4.6, 8.0, "Câu thứ ba."),
    Segment(9.0, 11.5, "Tạm biệt."),
]


def _run(video, tmp_path, mode, burn=False):
    logs = []
    s = DubSettings(tts_engine="tone", mix_mode=mode, burn_captions=burn, export_srt=True)
    pipe = DubbingPipeline(s, Keys(), Reporter(on_log=lambda m, l: logs.append((m, l))),
                           tts_engine=ToneTTS(), work_root=tmp_path / "work")
    out = pipe.render(video, SCRIPT, tmp_path / f"out_{mode}.mp4")
    return out, logs


def test_render_simple_mix(sample_video, tmp_path):
    out, _ = _run(sample_video, tmp_path, "simple")
    info = probe(out)
    assert info.has_video and info.has_audio
    assert abs(info.duration - 12.0) < 0.2
    assert out.with_suffix(".srt").exists()


def test_render_duck_mix_and_speedup_logged(sample_video, tmp_path):
    out, logs = _run(sample_video, tmp_path, "duck")
    assert probe(out).has_audio
    assert any("tăng tốc" in m for m, _ in logs)


def test_failed_clip_becomes_silence_not_crash(sample_video, tmp_path):
    class Flaky(ToneTTS):
        def synthesize(self, text, out_path):
            if "Tạm biệt" in text:
                raise RuntimeError("quota exceeded")
            super().synthesize(text, out_path)

    logs = []
    s = DubSettings(tts_engine="tone", mix_mode="simple", export_srt=False)
    pipe = DubbingPipeline(s, Keys(), Reporter(on_log=lambda m, l: logs.append(m)),
                           tts_engine=Flaky(), work_root=tmp_path / "w")
    out = pipe.render(sample_video, SCRIPT, tmp_path / "flaky.mp4")
    assert probe(out).has_video
    assert any("lỗi" in m for m in logs)


def test_video_without_audio_track(sample_video, tmp_path):
    import subprocess
    mute = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(sample_video), "-an", "-c:v", "copy", str(mute)], check=True)
    out, _ = _run(mute, tmp_path, "duck")
    assert probe(out).has_audio  # chỉ có giọng AI
