from core.subtitles import format_srt, normalize, parse_script, parse_time, Segment, split_long_segments


def test_parse_bracket_format():
    segs = parse_script("[0.5s -> 3.0s] Xin chào\n[00:00:03.500 -> 00:00:04.500] Tạm biệt")
    assert [s.text for s in segs] == ["Xin chào", "Tạm biệt"]
    assert segs[1].start == 3.5


def test_parse_csv_legacy_format():
    segs = parse_script("1.5, 3.0, Xin chào\n3.2, 5, Tạm biệt")
    assert segs[0].start == 1.5 and segs[0].text == "Xin chào"


def test_parse_srt_multiline():
    text = "1\n00:00:01,000 --> 00:00:02,500\ndòng một\ndòng hai\n\n2\n00:00:03,000 --> 00:00:04,000\nKết\n"
    segs = parse_script(text)
    assert segs[0].text == "dòng một dòng hai"
    assert segs[1].end == 4.0


def test_normalize_fixes_overlap_and_zero_length():
    segs = normalize([Segment(0, 3, "a"), Segment(2, 2, "b")])
    assert segs[0].end == 2.0          # cắt chồng lấn
    assert segs[1].end > segs[1].start  # câu độ dài 0 được nới ra


def test_srt_roundtrip_format():
    out = format_srt([Segment(1.5, 3.0, "Xin chào")])
    assert "00:00:01,500 --> 00:00:03,000" in out


def test_split_long_segment_by_punctuation():
    seg = Segment(0, 20, "Câu một dài. Câu hai dài hơn nhiều. Câu ba.")
    parts = split_long_segments([seg], max_seconds=8)
    assert len(parts) >= 2
    assert parts[0].start == 0 and abs(parts[-1].end - 20) < 1e-6


def test_parse_time_variants():
    assert parse_time("1:30") == 90
    assert parse_time("2,5") == 2.5
