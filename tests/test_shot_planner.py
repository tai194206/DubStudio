import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.shot_planner import Beat, Shot, merge_short_tail, plan_shots  # noqa: E402


def times_for(beats, per_token=0.4, gap=0.05):
    """Mốc giả: mỗi token 0.4s, khoảng nghỉ 0.05s giữa các token."""
    out, t = [], 0.0
    for b in beats:
        for _ in b.text.split():
            out.append((round(t, 3), round(t + per_token, 3)))
            t += per_token + gap
    return out


def beats_sample():
    return [
        Beat(1, "Ngày ấy đất nước còn nô lệ.", "bến cảng", intensity=1, intent="establish"),
        Beat(2, "Người thanh niên bước lên tàu.", "bến cảng", intensity=2, intent="explain"),
        Beat(3, "Con tàu rời bến giữa sóng lớn.", "biển", intensity=3, intent="emotion"),
    ]


def test_shots_cover_timeline_without_gaps():
    beats = beats_sample()
    shots = plan_shots(beats, times_for(beats))
    assert shots[0].start == 0.0
    for a, b in zip(shots, shots[1:]):
        assert a.end == pytest.approx(b.start, abs=1e-3)


def test_location_change_forces_cut_at_beat_boundary():
    beats = beats_sample()
    shots = plan_shots(beats, times_for(beats))
    locs = [s.location for s in shots]
    assert locs[0] == "bến cảng" and locs[-1] == "biển"
    biển = [s for s in shots if s.location == "biển"][0]
    assert biển.transition_in == "cut"


def test_same_location_continues_camera_without_reset():
    beats = [
        Beat(1, "Một câu đầu.", "làng", intensity=2, intent="explain"),
        Beat(2, "Một câu nữa ở cùng chỗ.", "làng", intensity=2, intent="explain"),
    ]
    shots = plan_shots(beats, times_for(beats, per_token=0.5))
    cont = [s for s in shots if s.transition_in == "continuous"]
    if len(shots) > 1:
        assert cont, "hai shot liên tiếp cùng địa điểm phải là continuous"
        assert cont[0].zoom_from == pytest.approx(shots[cont[0].id - 2].zoom_to, abs=1e-6)


def test_no_shot_longer_than_max_when_splittable():
    beats = [Beat(1, " ".join(["từ"] * 30) + ".", "làng", intensity=2)]
    shots = plan_shots(beats, times_for(beats, per_token=0.3), max_shot=5.0, min_shot=1.0)
    assert len(shots) > 1
    assert all(s.duration <= 5.0 + 0.4 for s in shots)       # sai số một token


def test_cuts_prefer_sentence_ends_and_fall_back_inside_sentence_only_when_needed():
    beats = [Beat(1, "Câu một dài hơn. Câu hai ngắn. Câu ba cũng khá dài ở đây.", "làng", intensity=1)]
    toks = beats[0].text.split()
    shots = plan_shots(beats, times_for(beats, per_token=0.6), max_shot=4.5, min_shot=1.0)
    assert all(s.duration <= 4.5 + 0.7 for s in shots)            # sai số một token
    first_cut_tok = toks[shots[0].token_range[1] - 1]
    assert first_cut_tok.endswith("ngắn."), "shot đầu phải kết thúc ở ranh giới câu khi vừa giới hạn"


def _shot(i, start, end, loc="làng"):
    return Shot(id=i, start=start, end=end, location=loc, beat_ids=[i], motion="hold",
                zoom_from=1.0, zoom_to=1.0, transition_in="cut", token_range=(i, i + 1))


def test_merge_short_tail_merges_when_same_location_and_fits():
    shots = [_shot(1, 0.0, 3.0), _shot(2, 3.0, 3.6)]
    merge_short_tail(shots, min_shot=1.5, max_shot=4.0)
    assert len(shots) == 1 and shots[0].end == pytest.approx(3.6)


def test_merge_short_tail_keeps_tail_when_location_differs():
    shots = [_shot(1, 0.0, 3.0, "a"), _shot(2, 3.0, 3.6, "b")]
    merge_short_tail(shots, min_shot=1.5, max_shot=4.0)
    assert len(shots) == 2


def test_merge_short_tail_keeps_tail_when_total_exceeds_max():
    shots = [_shot(1, 0.0, 3.8), _shot(2, 3.8, 4.4)]
    merge_short_tail(shots, min_shot=1.5, max_shot=4.0)
    assert len(shots) == 2


def test_long_unpunctuated_sentence_is_split_near_max_not_into_single_tokens():
    beats = [Beat(1, " ".join(["từ"] * 40) + ".", "làng", intensity=1)]
    shots = plan_shots(beats, times_for(beats, per_token=0.4), max_shot=5.0, min_shot=1.0)
    assert len(shots) >= 2
    for s in shots[:-1]:
        assert s.duration >= 0.5 * 5.0, f"shot {s.id} bị băm quá nhỏ: {s.duration:.2f}s"
        assert s.duration <= 5.0 + 1e-6


def test_intensity_drives_push_amount():
    calm = plan_shots([Beat(1, "Một.", "a", intensity=1, intent="explain")], times_for([Beat(1, "Một.", "a")]))
    hot = plan_shots([Beat(1, "Một.", "a", intensity=3, intent="emotion")], times_for([Beat(1, "Một.", "a")]))
    assert (hot[0].zoom_to - hot[0].zoom_from) > (calm[0].zoom_to - calm[0].zoom_from)


def test_token_count_mismatch_is_rejected():
    beats = beats_sample()
    with pytest.raises(ValueError, match="không khớp"):
        plan_shots(beats, times_for(beats)[:-1])


def test_empty_input():
    assert plan_shots([], []) == []


def test_single_long_token_group_is_not_split_but_still_covers():
    beats = [Beat(1, "rất " * 3 + "dài.", "a", intensity=1)]
    shots = plan_shots(beats, times_for(beats, per_token=4.0), max_shot=6.0)
    assert shots[-1].end == pytest.approx(times_for(beats, per_token=4.0)[-1][1], abs=1e-3)
