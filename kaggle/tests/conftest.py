import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """Video mau 30 giay (hinh test + am sine) tao bang FFmpeg, khong can internet."""
    out = tmp_path_factory.mktemp("fx") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("fx2") / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=5",
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out
