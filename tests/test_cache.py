import pytest
from pathlib import Path

from core.audio_track import render_clips
from core.cache import AudioCache, compute_cache_key
from core.models import Segment, WordTiming
from core.tts import TTSEngine, ToneTTS


class CountingTTS(TTSEngine):
    name = "counting"
    ext = ".wav"

    def __init__(self, voice_id: str = "v1", params: dict | None = None):
        self._voice_id = voice_id
        self._params = params or {"model": "m1"}
        self.call_count = 0
        self._tone = ToneTTS()

    @property
    def voice_id(self) -> str:
        return self._voice_id

    @property
    def params(self) -> dict:
        return self._params

    def synthesize(self, text: str, out_path: Path):
        self.call_count += 1
        return self._tone.synthesize(text, out_path)


def test_cache_hit_avoids_tts(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    segs = [Segment(0.0, 2.0, "Câu thứ nhất"), Segment(2.5, 4.5, "Câu thứ hai")]

    # Lần 1: Chưa có cache → gọi TTS 2 lần
    eng1 = CountingTTS()
    render_clips(segs, eng1, tmp_path / "work1", max_speed=1.6, total_duration=5.0, cache=cache)
    assert eng1.call_count == 2

    # Lần 2: Cùng kịch bản và tham số → cache hit 100%, không gọi TTS lần nào
    eng2 = CountingTTS()
    render_clips(segs, eng2, tmp_path / "work2", max_speed=1.6, total_duration=5.0, cache=cache)
    assert eng2.call_count == 0


def test_cache_partial_hit_when_editing_one_sentence(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    segs_initial = [
        Segment(0.0, 2.0, "Câu một"),
        Segment(2.5, 4.5, "Câu hai giữ nguyên"),
        Segment(5.0, 7.0, "Câu ba"),
    ]

    eng1 = CountingTTS()
    render_clips(segs_initial, eng1, tmp_path / "work1", max_speed=1.6, total_duration=8.0, cache=cache)
    assert eng1.call_count == 3

    # Sửa câu 1, giữ nguyên câu 2 và 3
    segs_modified = [
        Segment(0.0, 2.0, "Câu một đã được sửa"),
        Segment(2.5, 4.5, "Câu hai giữ nguyên"),
        Segment(5.0, 7.0, "Câu ba"),
    ]

    eng2 = CountingTTS()
    render_clips(segs_modified, eng2, tmp_path / "work2", max_speed=1.6, total_duration=8.0, cache=cache)
    # Chỉ gọi TTS đúng 1 lần cho câu đã sửa
    assert eng2.call_count == 1


def test_cache_miss_when_engine_params_change(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    segs = [Segment(0.0, 2.0, "Kiểm tra thay đổi params")]

    eng_pitch0 = CountingTTS(params={"pitch": "+0Hz"})
    render_clips(segs, eng_pitch0, tmp_path / "work1", max_speed=1.6, total_duration=3.0, cache=cache)
    assert eng_pitch0.call_count == 1

    # Đổi tham số pitch -> cache miss -> gọi lại TTS
    eng_pitch5 = CountingTTS(params={"pitch": "+5Hz"})
    render_clips(segs, eng_pitch5, tmp_path / "work2", max_speed=1.6, total_duration=3.0, cache=cache)
    assert eng_pitch5.call_count == 1


def test_corrupt_or_empty_cache_auto_regenerates(tmp_path):
    cache = AudioCache(tmp_path / "cache")
    text = "Câu cần kiểm tra cache hỏng"
    key = compute_cache_key("counting", "v1", text, {"model": "m1"})

    # 1. Tạo file rỗng 0 bytes
    empty_file = cache.cache_dir / f"{key}.wav"
    empty_file.write_bytes(b"")
    assert cache.get(key, ".wav") is None

    # 2. Tạo file dữ liệu rác không đọc được bởi ffprobe
    corrupt_file = cache.cache_dir / f"{key}.wav"
    corrupt_file.write_bytes(b"CORRUPT_NOT_A_VALID_AUDIO_FILE")
    assert cache.get(key, ".wav") is None

    # 3. Khi render qua cache hỏng, hệ thống tự động phát hiện và sinh lại file hợp lệ
    segs = [Segment(0.0, 2.0, text)]
    eng = CountingTTS()
    render_clips(segs, eng, tmp_path / "work", max_speed=1.6, total_duration=3.0, cache=cache)
    assert eng.call_count == 1

    # File trong cache giờ đây đã hợp lệ và ffprobe đọc được
    res = cache.get(key, ".wav")
    assert res is not None
    audio_path, words = res
    assert audio_path.exists()
    assert audio_path.stat().st_size > 0
