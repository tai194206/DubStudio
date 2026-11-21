import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.continuous_motion import (Run, build_run_filter, group_continuous_runs,  # noqa: E402
                                    zoom_expression)
from core.shot_planner import Shot  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def shot(i, s, e, loc, za, zb, tr):
    return Shot(id=i, start=s, end=e, location=loc, beat_ids=[i], motion="push_in",
                zoom_from=za, zoom_to=zb, transition_in=tr, token_range=(0, 1))


def test_continuous_shots_form_one_run_and_cut_starts_new_run():
    shots = [
        shot(1, 0.0, 3.0, "phong", 1.0, 1.08, "cut"),
        shot(2, 3.0, 6.0, "phong", 1.08, 1.18, "continuous"),
        shot(3, 6.0, 9.0, "anh_sang", 1.0, 1.1, "cut"),
    ]
    runs = group_continuous_runs(shots)
    assert [r.location for r in runs] == ["phong", "anh_sang"]
    assert runs[0].shot_ids == [1, 2] and runs[0].duration == pytest.approx(6.0)
    assert runs[1].shot_ids == [3]


def test_pieces_are_relative_to_run_start_and_keep_zoom_chain():
    shots = [shot(1, 2.0, 5.0, "a", 1.0, 1.08, "cut"), shot(2, 5.0, 8.0, "a", 1.08, 1.18, "continuous")]
    run = group_continuous_runs(shots)[0]
    assert run.pieces[0][0] == 0.0 and run.pieces[1][0] == pytest.approx(3.0)
    assert run.pieces[0][3] == pytest.approx(run.pieces[1][2])   # zoom nối tiếp, không reset


def test_zoom_expression_has_one_branch_per_piece_and_interpolates():
    expr = zoom_expression([(0.0, 3.0, 1.0, 1.08), (3.0, 6.0, 1.08, 1.18)], fps=30)
    assert expr.count("if(lt(on,") == 2
    assert "(on-0)/90" in expr and "(on-90)/90" in expr


def test_empty_pieces_gives_neutral_zoom():
    assert zoom_expression([]) == "1.0"


def test_filter_string_has_frame_count_and_output_size():
    run = Run("a", 0.0, 3.0, [(0.0, 3.0, 1.0, 1.1)], [1])
    f = build_run_filter(run)
    assert "d=90" in f and "s=1080x1920" in f and "setsar=1" in f


@pytest.mark.skipif(not FFMPEG, reason="cần ffmpeg")
def test_ffmpeg_renders_two_piece_run_with_exact_length(tmp_path):
    plate = tmp_path / "plate.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x3c5a8c:s=768x1344", "-frames:v", "1", str(plate)], check=True)
    run = Run("a", 0.0, 4.0, [(0.0, 2.0, 1.0, 1.08), (2.0, 4.0, 1.08, 1.2)], [1, 2])
    out = tmp_path / "run.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", str(plate),
                    "-vf", build_run_filter(run), "-t", "4", "-c:v", "libx264", "-crf", "20", "-an",
                    str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                            "stream=width,height,nb_frames", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    w, h, nb = probe.split(",")
    assert (int(w), int(h)) == (1080, 1920)
    assert abs(int(nb) - 120) <= 1
