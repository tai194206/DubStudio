import struct
import wave
import pytest

from core.errors import DubberError
from core.transcriber import _read_wav_float32


def _create_wav(path, nchannels=1, sampwidth=2, framerate=16000, num_frames=1600):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        # 1600 frames of sine-like or dummy 16-bit samples
        if sampwidth == 2:
            data = struct.pack(f"<{num_frames * nchannels}h", *([1000] * (num_frames * nchannels)))
        elif sampwidth == 1:
            data = bytes([128] * (num_frames * nchannels))
        else:
            data = bytes(num_frames * nchannels * sampwidth)
        w.writeframes(data)


def test_wav_helper_reads_valid_16khz_mono(tmp_path):
    wav_path = tmp_path / "valid.wav"
    _create_wav(wav_path, nchannels=1, sampwidth=2, framerate=16000, num_frames=1600)

    arr = _read_wav_float32(wav_path)
    assert len(arr) == 1600
    assert abs(arr[0] - (1000.0 / 32768.0)) < 1e-4
    assert arr.dtype.name == "float32"


def test_wav_helper_raises_on_wrong_framerate(tmp_path):
    wav_path = tmp_path / "wrong_rate.wav"
    _create_wav(wav_path, nchannels=1, sampwidth=2, framerate=44100, num_frames=441)
    with pytest.raises(DubberError, match="16 kHz"):
        _read_wav_float32(wav_path)


def test_wav_helper_raises_on_stereo(tmp_path):
    wav_path = tmp_path / "stereo.wav"
    _create_wav(wav_path, nchannels=2, sampwidth=2, framerate=16000, num_frames=1600)
    with pytest.raises(DubberError, match="mono"):
        _read_wav_float32(wav_path)


def test_wav_helper_raises_on_wrong_sampwidth(tmp_path):
    wav_path = tmp_path / "8bit.wav"
    _create_wav(wav_path, nchannels=1, sampwidth=1, framerate=16000, num_frames=1600)
    with pytest.raises(DubberError, match="16-bit"):
        _read_wav_float32(wav_path)
