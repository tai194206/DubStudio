import base64
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from story import story as v1  # noqa: E402
from story import v2  # noqa: E402

USER_SAMPLE = {
    "version": "2.0",
    "title": "Hành trình tìm đường cứu nước - Bến Nhà Rồng 1911",
    "aspect_ratio": "9:16",
    "total_words": 285,
    "style_prefix": "2D flat vector illustration, OverSimplified historical style, bold black outlines, solid flat colors, vertical 9:16",
    "character_bible": "Young Vietnamese man in 1911, slim build, short neat black hair, wearing a simple white traditional button shirt and dark trousers",
    "full_script_for_elevenlabs": "Ngày mồng 5 tháng 6 năm 1911, tại bến cảng Nhà Rồng...",
    "scenes": [
        {
            "id": 1,
            "speech": "Ngày mồng 5 tháng 6 năm 1911, tại bến cảng Nhà Rồng, một quyết định lịch sử đã được đưa ra.",
            "source_note": "Tài liệu Bảo tàng Hồ Chí Minh - Sự kiện tàu Amiral Latouche-Tréville.",
            "asset_type": "flow_video",
            "image_prompt": "Wide low-angle shot, 2D flat vector illustration, OverSimplified historical style, young Vietnamese man in 1911 wearing a simple white shirt and dark trousers standing at Saigon harbor looking at a massive French steamship, warm morning sunlight, vertical 9:16",
            "motion_prompt": "Slow camera push-in toward the young man, gentle harbor water ripples, steam rising from the ship chimney.",
            "ken_burns": "zoom_in",
            "sfx": "ship_horn",
            "on_screen_text": "5/6/1911 - BẾN NHÀ RỒNG",
        }
    ],
}

# Kịch bản kỹ thuật, không phải nội dung lịch sử: chỉ dùng để kiểm thử đồng bộ.
DEMO_SCENES = [
    "Đây là cảnh thử nghiệm một.",
    "Cảnh thử nghiệm thứ hai có nhiều từ hơn.",
    "Cảnh cuối dùng để kiểm tra mốc thời gian.",
]


def demo_data(scenes=DEMO_SCENES):
    return {
        "version": "2.0",
        "title": "Demo kỹ thuật",
        "aspect_ratio": "9:16",
        "style_prefix": "flat cartoon",
        "scenes": [
            {"id": i + 1, "speech": t, "asset_type": "static_image" if i else "flow_video",
             "image_prompt": f"stick figure {i + 1}", "ken_burns": "zoom_in", "sfx": "none",
             "on_screen_text": "TIÊU ĐỀ" if i == 0 else ""}
            for i, t in enumerate(scenes)
        ],
    }


# ---------------------------------------------------------------- giả lập ElevenLabs
class FakeAligner:
    """Tạo audio thật (sóng sin theo từng ký tự) và alignment theo ký tự, đúng cấu trúc của ElevenLabs."""

    def __init__(self, drop_words=0):
        self.calls = 0
        self.drop_words = drop_words

    def __call__(self, text, cache_dir):
        self.calls += 1
        chars = list(text)
        starts, ends, t = [], [], 0.1
        for ch in chars:
            d = 0.08 if ch.isspace() else 0.07
            starts.append(round(t, 3))
            ends.append(round(t + d, 3))
            t += d
        x = np.zeros(int(round(t * v1.SR)) + v1.SR // 2, dtype=np.float32)
        for s, e, ch in zip(starts, ends, chars):
            if not ch.isspace():
                i, j = int(s * v1.SR), int(e * v1.SR)
                x[i:j] = 0.3 * np.sin(2 * np.pi * 220 * np.arange(j - i) / v1.SR)
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        wav = cache_dir / f"fake_{self.calls}.wav"
        v1.write_wav(wav, x)
        align = {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends}
        if self.drop_words:
            # Mô phỏng bản gửi đi khác bản đối chiếu: thiếu một từ trong alignment
            cut = len(chars)
            spaces = [i for i, c in enumerate(chars) if c == " "]
            if spaces:
                cut = spaces[-self.drop_words] if len(spaces) >= self.drop_words else 0
                align = {k: (v[:cut] if isinstance(v, list) else v) for k, v in align.items()}
        return wav, align


def make_project(root: Path, data: dict, flow_scene: int = 0) -> Path:
    proj = root / "projects" / "demo_v2"
    (proj / "assets").mkdir(parents=True)
    (proj / "storyboard_v2.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for sc in data["scenes"]:
        sid = sc["id"]
        if sid == flow_scene:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=720x1280:rate=30:duration=8", "-an", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(proj / "assets" / f"{sid:03d}.mp4")], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"color=c=0x{(sid * 50) % 255:02x}66aa:s=768x1344", "-frames:v", "1",
                            str(proj / "assets" / f"{sid:03d}.png")], check=True)
    return proj


# ================================================================ schema
def test_user_sample_is_valid_and_reports_placeholder_script():
    sb = v2.validate_v2(copy.deepcopy(USER_SAMPLE))
    assert len(sb.scenes) == 1 and sb.scenes[0].asset_type == "flow_video"
    assert sb.scenes[0].motion == "zoom_in" and sb.scenes[0].sfx == "ship_horn"
    msgs = " ".join(sb.warnings)
    assert "full_script_for_elevenlabs" in msgs          # bản có dấu '...' bị đánh dấu
    assert "250-350" in msgs or "18-35" in msgs          # 1 cảnh, chưa đủ độ dài


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(version="1.0"), "version"),
    (lambda d: d.update(aspect_ratio="16:9"), "aspect_ratio"),
    (lambda d: d["scenes"][0].update(asset_type="video"), "asset_type"),
    (lambda d: d["scenes"][0].update(ken_burns="spin"), "ken_burns"),
    (lambda d: d["scenes"][0].update(sfx="explode"), "sfx"),
    (lambda d: d["scenes"][0].update(speech=""), "speech"),
    (lambda d: d["scenes"][0].update(tts_text="một hai"), "tts_text"),
    (lambda d: d["scenes"].append(dict(d["scenes"][0])), "trùng"),
])
def test_invalid_v2_fields_are_rejected(mutate, needle):
    data = copy.deepcopy(USER_SAMPLE)
    mutate(data)
    with pytest.raises(v1.StoryError, match=needle):
        v2.validate_v2(data)


def test_tts_text_with_same_word_count_is_accepted():
    data = copy.deepcopy(USER_SAMPLE)
    data["scenes"][0]["tts_text"] = "Ngày mồng năm tháng sáu năm một nghìn chín trăm mười một tại bến cảng Nhà Rồng một quyết định lịch sử đã được đưa ra."
    with pytest.raises(v1.StoryError, match="tts_text"):      # khác số từ -> từ chối
        v2.validate_v2(data)
    data["scenes"][0]["tts_text"] = data["scenes"][0]["speech"].replace("5 tháng 6", "năm tháng sáu")
    sb = v2.validate_v2(data)
    assert sb.scenes[0].tts_text != sb.scenes[0].speech and sb.scenes[0].speech.count(" ") == sb.scenes[0].tts_text.count(" ")


def test_full_text_is_joined_from_scenes_not_declared_field():
    sb = v2.validate_v2(demo_data())
    assert sb.full_text() == " ".join(DEMO_SCENES)


# ================================================================ mốc từ & ánh xạ
def test_chars_to_words_groups_by_whitespace():
    align = {"characters": list("Ab cd  e"),
             "character_start_times_seconds": [0, .1, .2, .3, .4, .5, .6, .7],
             "character_end_times_seconds": [.1, .2, .3, .4, .5, .6, .7, .8]}
    words = v2.chars_to_words(align)
    assert [w["word"] for w in words] == ["Ab", "cd", "e"]
    assert words[1]["start"] == pytest.approx(0.3) and words[1]["end"] == pytest.approx(0.5)


def test_chars_to_words_rejects_mismatched_arrays():
    with pytest.raises(v2.AlignmentError):
        v2.chars_to_words({"characters": ["a", "b"], "character_start_times_seconds": [0.0],
                           "character_end_times_seconds": [0.1, 0.2]})


def test_map_scenes_is_contiguous_and_covers_audio():
    sb = v2.validate_v2(demo_data())
    words = v2.chars_to_words(FakeAligner()(sb.full_text(), Path(tempfile.gettempdir()) / "none")[1])
    windows = v2.map_scenes(sb.scenes, words, audio_seconds=words[-1]["end"])
    assert windows[0][0] == 0.0
    for (s1, e1, _), (s2, _, _) in zip(windows, windows[1:]):
        assert e1 == pytest.approx(s2)                       # nối khít, không khoảng trống
    assert windows[-1][1] == pytest.approx(words[-1]["end"] + v2.TAIL_SECONDS, abs=1e-6)
    spans = [sp for _, _, sp in windows]
    counts = [len(t.split()) for t in DEMO_SCENES]                 # 5, 8, 8 từ
    expected, c = [], 0
    for n in counts:
        expected.append((c, c + n))
        c += n
    assert spans == expected


def test_map_scenes_count_mismatch_gives_clear_error():
    sb = v2.validate_v2(demo_data())
    words = v2.chars_to_words(FakeAligner()(sb.full_text(), Path(tempfile.gettempdir()) / "none")[1])
    with pytest.raises(v2.AlignmentError, match="kịch bản đã gửi"):
        v2.map_scenes(sb.scenes, words[:-2], audio_seconds=5.0)


def test_karaoke_uses_real_word_times_and_escapes():
    tokens = ["một", "hai", "ba", "bốn", "năm", "sáu"]
    timings = [{"word": t, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, t in enumerate(tokens)]
    ev = v2.karaoke_events(tokens, timings)
    assert len(ev) == 2
    assert ev[0][0] == pytest.approx(0.0) and ev[0][1] == pytest.approx(2.4)
    assert ev[1][0] == pytest.approx(2.5)
    assert "\\kf" in ev[0][2] and ev[0][2].count("\\kf") == 5


def test_ass_time_handles_rollover_at_minute_boundary():
    assert v1.ass_time(59.999) == "0:01:00.00"              # lỗi cũ trả "0:00:60.00"


# ================================================================ cache ElevenLabs (HTTP giả)
class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def ok_payload(text):
    chars = list(text)
    st = [i * 0.05 for i in range(len(chars))]
    en = [i * 0.05 + 0.04 for i in range(len(chars))]
    return {"audio_base64": base64.b64encode(b"FAKE-MP3-BYTES").decode(),
            "alignment": {"characters": chars, "character_start_times_seconds": st, "character_end_times_seconds": en}}


def test_elevenlabs_second_call_uses_cache_and_no_http(tmp_path):
    posts = []

    def http(url, **kw):
        posts.append(url)
        return FakeResponse(200, ok_payload(kw["json"]["text"]))

    al = v2.ElevenLabsAligner("k", "voice1", http_post=http, sleep=lambda s: None)
    mp3a, alla = al("xin chào", tmp_path)
    mp3b, allb = al("xin chào", tmp_path)
    assert len(posts) == 1 and alla == allb
    assert mp3a.read_bytes() == b"FAKE-MP3-BYTES" and mp3a == mp3b


def test_elevenlabs_cache_changes_with_text_and_voice(tmp_path):
    a = v2.ElevenLabsAligner("k", "voice1", http_post=lambda *a, **k: None)
    b = v2.ElevenLabsAligner("k", "voice2", http_post=lambda *a, **k: None)
    assert a.cache_key("x") != a.cache_key("y") and a.cache_key("x") != b.cache_key("x")


def test_elevenlabs_retries_429_then_succeeds_and_fails_fast_on_401(tmp_path):
    seq = [FakeResponse(429, text="slow down"), FakeResponse(200, ok_payload("ab"))]
    sleeps = []
    al = v2.ElevenLabsAligner("k", "v", http_post=lambda *a, **k: seq.pop(0), sleep=sleeps.append)
    al("ab", tmp_path)
    assert sleeps == [2] and al.calls == 2

    al2 = v2.ElevenLabsAligner("bad", "v", http_post=lambda *a, **k: FakeResponse(401, text="invalid key"),
                               sleep=lambda s: None)
    with pytest.raises(v2.AlignmentError, match="401"):
        al2("zz", tmp_path / "other")
    assert al2.calls == 1                                   # lỗi 401 không thử lại


def test_elevenlabs_rejects_incomplete_response(tmp_path):
    al = v2.ElevenLabsAligner("k", "v", http_post=lambda *a, **k: FakeResponse(200, {"audio_base64": ""}),
                              sleep=lambda s: None)
    with pytest.raises(v2.AlignmentError, match="thiếu"):
        al("ab", tmp_path)


def test_missing_api_key_or_voice_fails_before_any_request():
    with pytest.raises(v1.StoryError, match="ELEVENLABS_API_KEY"):
        v2.ElevenLabsAligner("", "v")
    with pytest.raises(v1.StoryError, match="voice_id"):
        v2.ElevenLabsAligner("k", "")


# ================================================================ dựng phim end-to-end
def probe(path):
    text = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stderr
    return {"video": "Video:" in text and "1080x1920" in text, "audio": "Audio:" in text,
            "duration": v1.media_duration(path)}


def test_build_v2_end_to_end_uses_real_alignment_timing(tmp_path):
    proj = make_project(tmp_path, demo_data(), flow_scene=1)
    out = tmp_path / "out" / "demo.mp4"
    aligner = FakeAligner()
    logs = []
    v2.build_v2(proj, out, v2.V2Options(cache_dir=tmp_path / "c", keep_temp=True), aligner=aligner, log=logs.append)

    assert aligner.calls == 1                               # đúng MỘT lần gọi cho cả kịch bản
    info = probe(out)
    assert info["video"] and info["audio"]
    timeline = json.loads(out.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in timeline] == [1, 2, 3]
    assert timeline[0]["start"] == 0.0
    for a, b in zip(timeline, timeline[1:]):
        assert a["end"] == pytest.approx(b["start"], abs=1e-3)
    assert info["duration"] == pytest.approx(timeline[-1]["end"], abs=0.15)
    assert not any("lặp" in m for m in logs)              # các cảnh đều ngắn hơn 8s, không có cảnh báo lặp


def test_long_flow_scene_warns_about_clip_loop(tmp_path):
    long_text = " ".join(["từ"] * 50) + "."                 # ~9-10 giây theo mốc FakeAligner (> 8 giây)
    data = demo_data([long_text, "Cảnh hai ngắn."])
    proj = make_project(tmp_path, data, flow_scene=1)
    logs = []
    v2.build_v2(proj, tmp_path / "o.mp4", v2.V2Options(cache_dir=tmp_path / "c", burn_subtitles=False),
                aligner=FakeAligner(), log=logs.append)
    assert any("Cảnh 1" in m and "lặp" in m for m in logs)


def test_voice_onset_matches_each_scene_start(tmp_path):
    proj = make_project(tmp_path, demo_data(), flow_scene=1)
    out = tmp_path / "o.mp4"
    aligner = FakeAligner()
    v2.build_v2(proj, out, v2.V2Options(cache_dir=tmp_path / "c", keep_temp=True), aligner=aligner, log=lambda m: None)
    voice = v1.read_wav(tmp_path / "o_work" / "voice.wav")
    timeline = json.loads(out.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    sb = v2.validate_v2(demo_data())
    words = v2.chars_to_words(aligner(sb.full_text(), tmp_path / "c2")[1])
    for entry in timeline:
        first_word_start = words[sum(len(s.split()) for s in DEMO_SCENES[:entry["id"] - 1])]["start"]
        i = int(first_word_start * v1.SR)
        assert np.abs(voice[i:i + int(0.05 * v1.SR)]).max() > 0.05, f"cảnh {entry['id']} không có tiếng đúng mốc"


def test_build_v2_stops_before_render_when_alignment_mismatches(tmp_path):
    proj = make_project(tmp_path, demo_data())
    out = tmp_path / "o.mp4"
    with pytest.raises(v2.AlignmentError):
        v2.build_v2(proj, out, v2.V2Options(cache_dir=tmp_path / "c"), aligner=FakeAligner(drop_words=2),
                    log=lambda m: None)
    assert not out.exists()


def test_build_v2_with_prepared_audio_and_alignment_skips_api(tmp_path):
    proj = make_project(tmp_path, demo_data())
    sb = v2.validate_v2(demo_data())
    wav, align = FakeAligner()(sb.full_text(), tmp_path / "pre")
    out = tmp_path / "o.mp4"
    v2.build_v2(proj, out, v2.V2Options(cache_dir=tmp_path / "c", burn_subtitles=False),
                audio=wav, alignment=align, log=lambda m: None)
    assert probe(out)["video"]


def test_write_full_script_is_what_gets_sent(tmp_path):
    proj = make_project(tmp_path, demo_data())
    p = v2.write_full_script(proj)
    assert p.read_text(encoding="utf-8").strip() == " ".join(DEMO_SCENES)


def test_cli_validate_reports_error_code(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"version": "2.0"}), encoding="utf-8")
    assert v2.main(["validate", str(p)]) == 1
    out = capsys.readouterr().out
    assert "aspect_ratio" in out and "thiếu trường 'title'" in out


def test_each_scene_window_contains_its_own_speech():
    """Giọng của cảnh phải nằm trọn trong khung của cảnh đó, không bị cắt đuôi sang cảnh sau."""
    sb = v2.validate_v2(demo_data())
    words = v2.chars_to_words(FakeAligner()(sb.full_text(), Path(tempfile.gettempdir()) / "none")[1])
    windows = v2.map_scenes(sb.scenes, words, audio_seconds=words[-1]["end"])
    for start, end, (a, b) in windows:
        speech_start, speech_end = words[a]["start"], words[b - 1]["end"]
        assert start <= speech_start + 1e-6, "giọng bắt đầu trước khung cảnh"
        assert end >= speech_end - 1e-6, "giọng bị cắt đuôi sang cảnh sau"
