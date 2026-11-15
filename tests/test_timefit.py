from core.ffmpeg_utils import atempo_chain
from core.subtitles import Segment
from core.timefit import compute_speed, fit_window


def test_no_speedup_when_fits():
    assert compute_speed(duration=2.0, window=3.0, max_speed=1.6) == 1.0


def test_speedup_capped_by_max():
    assert compute_speed(duration=10.0, window=2.0, max_speed=1.6) == 1.6


def test_window_borrows_silence_until_next_segment():
    segs = [Segment(0, 1, "a"), Segment(5, 6, "b")]
    assert fit_window(segs, 0, total_duration=10) == 4.95


def test_atempo_chain_for_large_speed():
    assert atempo_chain(1.5) == "atempo=1.50000"
    assert atempo_chain(3.0).startswith("atempo=2.0,")
