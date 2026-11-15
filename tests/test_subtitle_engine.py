from pathlib import Path

from core.ffmpeg_utils import probe
from core.models import Segment, WordTiming
from core.subtitle_engine import burn_ass_subtitles, generate_karaoke_ass
from core.tts import ToneTTS


def test_tone_tts_generates_valid_word_timings(tmp_path):
    tone = ToneTTS()
    text = "Xin chào các bạn đang theo dõi"
    out_audio = tmp_path / "tone.wav"
    words = tone.synthesize(text, out_audio)

    assert out_audio.exists()
    assert len(words) == 7
    assert [w.word for w in words] == ["Xin", "chào", "các", "bạn", "đang", "theo", "dõi"]

    # Kiểm tra tính liên tục và hợp lệ của mốc thời gian
    assert words[0].start == 0.0
    for w in words:
        assert w.end > w.start
    for i in range(len(words) - 1):
        assert abs(words[i].end - words[i + 1].start) < 1e-4


def test_generate_karaoke_ass_has_utf8_and_k_tags(tmp_path):
    segs = [
        Segment(
            start=1.0,
            end=3.5,
            text="Lồng tiếng tự động",
            words=[
                WordTiming("Lồng", 1.0, 1.8),
                WordTiming("tiếng", 1.8, 2.6),
                WordTiming("tự", 2.6, 3.0),
                WordTiming("động", 3.0, 3.5),
            ],
        )
    ]
    ass_path = tmp_path / "karaoke.ass"
    generate_karaoke_ass(segs, ass_path, font_size=26)

    assert ass_path.exists()
    # Kiểm tra đọc UTF-8 tường minh có dấu tiếng Việt không bị lỗi font
    content = ass_path.read_text(encoding="utf-8")
    assert "[V4+ Styles]" in content
    assert "Karaoke" in content
    assert r"\kf" in content or r"\k" in content
    assert "Lồng" in content
    assert "tiếng" in content
    assert "tự" in content
    assert "động" in content


def test_burn_ass_subtitles_on_fixture_video(sample_video, tmp_path):
    # Tạo file .ass karaoke mẫu
    segs = [
        Segment(
            start=0.5,
            end=4.0,
            text="Thử nghiệm phụ đề Karaoke",
            words=[
                WordTiming("Thử", 0.5, 1.2),
                WordTiming("nghiệm", 1.2, 2.0),
                WordTiming("phụ", 2.0, 2.8),
                WordTiming("đề", 2.8, 3.2),
                WordTiming("Karaoke", 3.2, 4.0),
            ],
        )
    ]
    ass_path = tmp_path / "burn_test.ass"
    generate_karaoke_ass(segs, ass_path)

    out_video = tmp_path / "burned_fixture.mp4"
    burn_ass_subtitles(sample_video, ass_path, out_video, work_dir=tmp_path / "tmp_burn")

    assert out_video.exists()
    info = probe(out_video)
    assert info.has_video
    assert abs(info.duration - 12.0) < 0.2
