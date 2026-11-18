import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tts  # noqa: E402
from core import ffmpeg_utils  # noqa: E402
from core.errors import TTSError  # noqa: E402
from core.models import WordTiming  # noqa: E402


def words_ending_at(t):
    return [WordTiming("a", 0.0, t / 2), WordTiming("b", t / 2, t)]


# ---------------------------------------------------------------- chốt kiểm tra cắt cụt
def test_integrity_passes_when_audio_covers_last_word():
    tts.check_edge_integrity(5.2, words_ending_at(5.0))


def test_integrity_passes_within_tolerance():
    tts.check_edge_integrity(4.8, words_ending_at(5.0))          # thiếu 0.2s, trong dung sai 0.25s


def test_integrity_rejects_truncated_audio_with_retry_marker():
    with pytest.raises(TTSError) as info:
        tts.check_edge_integrity(3.0, words_ending_at(5.0))
    assert "truncated" in str(info.value).lower()


def test_integrity_skips_when_no_word_timings():
    tts.check_edge_integrity(0.1, [])                            # không có mốc thì không kiểm tra được


def test_truncated_is_treated_as_transient():
    assert "truncated" in tts.RETRYABLE_MARKERS


# ---------------------------------------------------------------- luồng EdgeTTS với edge_tts giả
class FakeCommunicate:
    """Mỗi lần gọi stream() phát một chuỗi sự kiện; lần đầu tiên có thể cắt cụt."""
    plan = []          # danh sách (độ dài audio bytes, mốc cuối tính bằng giây)
    calls = 0
    saves = 0
    save_ok = True
    stream_empty = False

    async def save(self, path):
        type(self).saves += 1
        if type(self).save_ok:
            Path(path).write_bytes(b"\x00" * 1000)
        else:
            raise RuntimeError("save fails")

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def stream(self):
        type(self).calls += 1
        if type(self).stream_empty:
            return
            yield  # pragma: no cover
        _, last_end = type(self).plan.pop(0)
        yield {"type": "audio", "data": b"\x00" * 1000}
        half = last_end / 2
        yield {"type": "WordBoundary", "offset": 0, "duration": int(half * 1e7), "text": "a"}
        yield {"type": "WordBoundary", "offset": int(half * 1e7), "duration": int(half * 1e7), "text": "b"}


@pytest.fixture
def fake_edge(monkeypatch):
    mod = types.ModuleType("edge_tts")
    mod.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", mod)
    FakeCommunicate.calls = 0
    FakeCommunicate.saves = 0
    FakeCommunicate.save_ok = True
    FakeCommunicate.stream_empty = False
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    return FakeCommunicate


def fake_probe_factory(seconds_seq):
    seq = list(seconds_seq)

    def probe(path):
        return SimpleNamespace(duration=seq.pop(0))
    return probe


def test_edge_retries_when_audio_is_truncated_then_succeeds(fake_edge, monkeypatch, tmp_path):
    # Lần 1: mốc cuối 5.0s nhưng audio chỉ 3.0s (cắt cụt). Lần 2: đủ 5.1s.
    fake_edge.plan = [(0, 5.0), (0, 5.0)]
    monkeypatch.setattr(ffmpeg_utils, "probe", fake_probe_factory([3.0, 5.1]))
    out = tmp_path / "scene.mp3"
    words = tts.EdgeTTS("vi-VN-HoaiMyNeural").synthesize("Xin chào các bạn.", out)
    assert fake_edge.calls == 2
    assert out.exists() and out.stat().st_size == 1000
    assert not (tmp_path / "scene.part.mp3").exists()           # file tạm đã được dọn
    assert words[-1].end == pytest.approx(5.0)


def test_truncated_stream_never_reaches_destination_and_falls_back(fake_edge, monkeypatch, tmp_path):
    """Stream cắt cụt mọi lần: file cắt không được ghi vào đích; chuyển sang save() với mốc ước lượng."""
    fake_edge.plan = [(0, 5.0)] * 5
    monkeypatch.setattr(ffmpeg_utils, "probe", fake_probe_factory([1.0] * 5))
    out = tmp_path / "scene.mp3"
    eng = tts.EdgeTTS("vi-VN-HoaiMyNeural")
    words = eng.synthesize("Một câu dài hơn hẳn.", out)
    assert fake_edge.saves == 1
    assert eng.last_timing_source == "estimated"
    assert out.exists() and out.stat().st_size == 1000          # đây là audio của save(), không phải bản cắt
    assert not (tmp_path / "scene.part.mp3").exists()
    assert words[-1].end == pytest.approx(1.0)


def test_edge_empty_audio_is_retried(fake_edge, monkeypatch, tmp_path):
    class EmptyThenGood(FakeCommunicate):
        async def stream(self):
            type(self).calls += 1
            if type(self).calls == 1:
                yield {"type": "WordBoundary", "offset": 0, "duration": 1e7, "text": "x"}
                return
            yield {"type": "audio", "data": b"\x00" * 1000}
            yield {"type": "WordBoundary", "offset": 0, "duration": int(2e7), "text": "hai"}
    fake_edge_mod = types.ModuleType("edge_tts")
    fake_edge_mod.Communicate = EmptyThenGood
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_mod)
    EmptyThenGood.plan = [(0, 2.0), (0, 2.0)]
    EmptyThenGood.calls = 0
    monkeypatch.setattr(ffmpeg_utils, "probe", fake_probe_factory([2.0]))
    out = tmp_path / "s.mp3"
    tts.EdgeTTS("vi-VN-HoaiMyNeural").synthesize("Hai từ.", out)
    assert EmptyThenGood.calls == 2 and out.exists()


def test_tone_word_timings_still_cover_text(tmp_path):
    words = tts.ToneTTS().synthesize("một hai ba", tmp_path / "t.wav")
    assert [w.word for w in words] == ["một", "hai", "ba"]
    assert words[-1].end == pytest.approx(max(0.4, len("một hai ba") / 14.0), abs=1e-3)


def test_normalize_rate_accepts_factor_and_percent():
    assert tts._normalize_rate(1.0) == "+0%"
    assert tts._normalize_rate(1.15) == "+15%"
    assert tts._normalize_rate("+10%") == "+10%"
    assert tts._normalize_rate("garbage") == "+0%"


# ---------------------------------------------------------------- đường dự phòng save()
def test_estimate_word_timings_covers_audio_and_keeps_order():
    words = tts.estimate_word_timings("một hai ba bốn", 4.0)
    assert [w.word for w in words] == ["một", "hai", "ba", "bốn"]
    assert words[0].start == 0.0 and words[-1].end == pytest.approx(4.0)
    for a, b in zip(words, words[1:]):
        assert a.end == pytest.approx(b.start, abs=1e-3)


def test_estimate_word_timings_empty_inputs():
    assert tts.estimate_word_timings("", 3.0) == []
    assert tts.estimate_word_timings("abc", 0.0) == []


def test_stream_success_reports_boundary_source(fake_edge, monkeypatch, tmp_path):
    fake_edge.plan = [(0, 2.0)]
    monkeypatch.setattr(ffmpeg_utils, "probe", fake_probe_factory([2.0]))
    eng = tts.EdgeTTS("vi-VN-HoaiMyNeural")
    eng.synthesize("Hai từ.", tmp_path / "a.mp3")
    assert eng.last_timing_source == "boundary"


def test_falls_back_to_save_when_stream_keeps_failing(fake_edge, monkeypatch, tmp_path):
    fake_edge.stream_empty = True
    monkeypatch.setattr(ffmpeg_utils, "probe", fake_probe_factory([3.0]))
    eng = tts.EdgeTTS("vi-VN-HoaiMyNeural")
    out = tmp_path / "b.mp3"
    words = eng.synthesize("Một hai ba.", out)
    assert out.exists() and fake_edge.saves == 1
    assert eng.last_timing_source == "estimated"
    assert [w.word for w in words] == ["Một", "hai", "ba."]
    assert words[-1].end == pytest.approx(3.0)


def test_both_paths_failing_raises_and_writes_nothing(fake_edge, monkeypatch, tmp_path):
    fake_edge.stream_empty = True
    fake_edge.save_ok = False
    out = tmp_path / "c.mp3"
    with pytest.raises(TTSError):
        tts.EdgeTTS("vi-VN-HoaiMyNeural").synthesize("Câu lỗi.", out)
    assert not out.exists()
    assert not (tmp_path / "c.part.mp3").exists()
