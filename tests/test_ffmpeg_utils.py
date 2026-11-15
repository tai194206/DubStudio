from core.ffmpeg_utils import probe


def test_probe_reads_duration_and_streams(sample_video):
    info = probe(sample_video)
    assert abs(info.duration - 12.0) < 0.2
    assert info.has_audio and info.has_video
