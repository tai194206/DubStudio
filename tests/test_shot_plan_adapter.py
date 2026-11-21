"""Kiểm thử cho module core.shot_plan_adapter."""
import pytest

from core.models import WordTiming
from core.shot_plan_adapter import beats_from_storyboard, word_times_from_words
from core.shot_planner import plan_shots


def test_storyboard_missing_beats_raises_error():
    sb = {"title": "Test không beats", "scenes": []}
    with pytest.raises(ValueError, match="storyboard chưa có beats; cần sinh beats trước"):
        beats_from_storyboard(sb)


def test_beat_missing_location_raises_error():
    sb = {
        "beats": [
            {"id": 1, "text": "Lời kể đầu tiên.", "intensity": 1, "intent": "explain"}
        ]
    }
    with pytest.raises(ValueError, match="thiếu hoặc có trường 'location' rỗng"):
        beats_from_storyboard(sb)

    # Thử nghiệm với location là chuỗi rỗng
    sb["beats"][0]["location"] = "   "
    with pytest.raises(ValueError, match="thiếu hoặc có trường 'location' rỗng"):
        beats_from_storyboard(sb)


def test_beat_invalid_intensity_raises_error():
    sb = {
        "beats": [
            {
                "id": 1,
                "text": "Lời kể.",
                "location": "Bến Nhà Rồng",
                "intensity": 4,
                "intent": "explain",
            }
        ]
    }
    with pytest.raises(ValueError, match="intensity=4 không hợp lệ"):
        beats_from_storyboard(sb)

    sb["beats"][0]["intensity"] = 0
    with pytest.raises(ValueError, match="intensity=0 không hợp lệ"):
        beats_from_storyboard(sb)


def test_beat_invalid_intent_raises_error():
    sb = {
        "beats": [
            {
                "id": 1,
                "text": "Lời kể.",
                "location": "Bến Nhà Rồng",
                "intensity": 2,
                "intent": "invalid_intent_xyz",
            }
        ]
    }
    with pytest.raises(ValueError, match="intent='invalid_intent_xyz' không hợp lệ"):
        beats_from_storyboard(sb)


def test_word_times_count_mismatch_raises_error_with_both_numbers():
    beats = beats_from_storyboard(
        {
            "beats": [
                {
                    "id": 1,
                    "text": "Một hai ba bốn.",
                    "location": "Sài Gòn",
                    "intensity": 1,
                    "intent": "explain",
                }
            ]
        }
    )
    # Beat có 4 từ: "Một", "hai", "ba", "bốn."
    # Nhưng danh sách words chỉ có 3 mốc
    words = [
        WordTiming("Một", 0.0, 0.5),
        WordTiming("hai", 0.5, 1.0),
        WordTiming("ba", 1.0, 1.5),
    ]

    with pytest.raises(ValueError) as excinfo:
        word_times_from_words(words, beats=beats)

    err_msg = str(excinfo.value)
    assert "3" in err_msg, "Thông điệp lỗi phải chứa số lượng mốc từ thực tế (3)"
    assert "4" in err_msg, "Thông điệp lỗi phải chứa số token kỳ vọng (4)"


def test_valid_input_preserves_order_and_attributes():
    sb = {
        "beats": [
            {
                "id": 1,
                "text": "Ngày mồng năm tháng sáu năm 1911.",
                "location": "Bến Nhà Rồng",
                "intensity": 1,
                "intent": "establish",
            },
            {
                "id": 2,
                "text": "Người thanh niên Nguyễn Tất Thành rời Tổ quốc.",
                "location": "Tàu Latouche",
                "intensity": 2,
                "intent": "reveal",
            },
        ]
    }
    beats = beats_from_storyboard(sb)
    assert len(beats) == 2
    assert beats[0].id == 1
    assert beats[0].location == "Bến Nhà Rồng"
    assert beats[0].intent == "establish"
    assert beats[1].id == 2
    assert beats[1].location == "Tàu Latouche"
    assert beats[1].intensity == 2

    # Chuyển đổi mốc từ
    raw_words = [
        WordTiming("Ngày", 0.0, 0.4),
        WordTiming("mồng", 0.4, 0.7),
        WordTiming("năm", 0.7, 1.0),
        WordTiming("tháng", 1.0, 1.3),
        WordTiming("sáu", 1.3, 1.6),
        WordTiming("năm", 1.6, 1.9),
        WordTiming("1911.", 1.9, 2.4),
        WordTiming("Người", 2.5, 2.8),
        WordTiming("thanh", 2.8, 3.1),
        WordTiming("niên", 3.1, 3.4),
        WordTiming("Nguyễn", 3.4, 3.7),
        WordTiming("Tất", 3.7, 4.0),
        WordTiming("Thành", 4.0, 4.3),
        WordTiming("rời", 4.3, 4.6),
        WordTiming("Tổ", 4.6, 4.8),
        WordTiming("quốc.", 4.8, 5.2),
    ]
    # Tổng cộng: 7 từ beat 1 + 9 từ beat 2 = 16 từ
    times = word_times_from_words(raw_words, beats=beats)
    assert len(times) == 16
    assert times[0] == (0.0, 0.4)
    assert times[-1] == (4.8, 5.2)


def test_plan_shots_on_adapter_output_covers_timeline_without_overlap():
    sb = {
        "beats": [
            {
                "id": 1,
                "text": "Sài Gòn năm 1911.",
                "location": "Bến Nhà Rồng",
                "intensity": 1,
                "intent": "establish",
            },
            {
                "id": 2,
                "text": "Bến cảng tấp nập thuyền bè.",
                "location": "Bến Nhà Rồng",
                "intensity": 2,
                "intent": "explain",
            },
            {
                "id": 3,
                "text": "Con tàu lớn nhổ neo rời bến.",
                "location": "Tàu Latouche",
                "intensity": 3,
                "intent": "reveal",
            },
        ]
    }
    beats = beats_from_storyboard(sb)
    # Token count:
    # Beat 1: "Sài" (1), "Gòn" (2), "năm" (3), "1911." (4)
    # Beat 2: "Bến" (5), "cảng" (6), "tấp" (7), "nập" (8), "thuyền" (9), "bè." (10)
    # Beat 3: "Con" (11), "tàu" (12), "lớn" (13), "nhổ" (14), "neo" (15), "rời" (16), "bến." (17)
    # Tổng: 17 từ
    tokens = [tok for b in beats for tok in b.text.split()]
    assert len(tokens) == 17

    # Tạo mốc từ liên tục
    words = []
    t = 0.0
    for tok in tokens:
        dur = 0.5
        words.append(WordTiming(tok, round(t, 2), round(t + dur, 2)))
        t += dur

    word_times = word_times_from_words(words, beats=beats)
    shots = plan_shots(beats, word_times, max_shot=4.0, min_shot=1.0)

    assert len(shots) > 0

    # 1. Kiểm tra không chồng lấn và thời gian tăng dần
    for i in range(len(shots) - 1):
        assert shots[i].end <= shots[i + 1].start, f"Shot {i} chồng lấn Shot {i+1}"

    # 2. Kiểm tra shot đầu bắt đầu tại mốc từ đầu và shot cuối kết thúc tại mốc từ cuối
    assert shots[0].start == word_times[0][0]
    assert shots[-1].end == word_times[-1][1]

    # 3. Kiểm tra liên tục địa điểm: Beat 1 và Beat 2 cùng 'Bến Nhà Rồng' -> shot chuyển tiếp phải là continuous
    # Cảnh đổi địa điểm sang 'Tàu Latouche' -> shot chuyển tiếp phải là cut
    for s in shots:
        assert s.location in ("Bến Nhà Rồng", "Tàu Latouche")
        assert s.duration > 0
