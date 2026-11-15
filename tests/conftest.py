import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """Video mẫu 12 giây do FFmpeg tạo ra: hình test + âm sine. Không cần internet."""
    out = tmp_path_factory.mktemp("fixtures") / "sample.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=12",
           "-f", "lavfi", "-i", "sine=frequency=220:duration=12",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    return out
