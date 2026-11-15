import subprocess
import sys
from pathlib import Path

from core.ffmpeg_utils import probe


def test_cli_help():
    cmd = [sys.executable, "cli.py", "--help"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0
    assert "TDTU Auto Dubber" in result.stdout


def test_cli_render_tone(sample_video, tmp_path):
    script_file = tmp_path / "script.txt"
    script_file.write_text("[0.5s -> 2.5s] Xin chào CLI\n[3.0s -> 5.0s] Kiểm thử render", encoding="utf-8")
    out_file = tmp_path / "out_cli.mp4"

    cmd = [
        sys.executable, "cli.py", "render",
        "--video", str(sample_video),
        "--script", str(script_file),
        "--out", str(out_file),
        "--engine", "tone",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, f"CLI render failed: {result.stderr}"
    assert out_file.exists()
    info = probe(out_file)
    assert info.has_video and info.has_audio
