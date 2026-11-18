import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from story import story as st  # noqa: E402
from story.story import StoryError  # noqa: E402


def good_story(n=3, words_each=4):
    speech = " ".join(["từ"] * words_each) + "."
    return {
        "title": "Thử nghiệm",
        "aspect_ratio": "9:16",
        "style_suffix": "flat cartoon",
        "bgm": "",
        "scenes": [
            {"id": i, "speech": speech, "image_prompt": f"stick figure {i}", "motion": "zoom_in",
             "sfx": "none", "on_screen_text": "NĂM 1950" if i == 1 else ""}
            for i in range(1, n + 1)
        ],
    }


def make_png(path, w=768, h=1344, color="0x3366aa"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}",
                    "-frames:v", "1", str(path)], check=True)


def make_mp4(path, dur=2.0):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=576x1024:rate=30:duration={dur}",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def make_project(root: Path, data: dict, mp4_scene: int = 0) -> Path:
    proj = root / "projects" / "demo"
    (proj / "assets").mkdir(parents=True)
    (proj / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for sc in data["scenes"]:
        sid = sc["id"]
        if sid == mp4_scene:
            make_mp4(proj / "assets" / f"{sid:03d}.mp4")
        else:
            make_png(proj / "assets" / f"{sid:03d}.png", color=f"0x{(sid * 40) % 255:02x}88cc")
    return proj


# ================================================================ kiểm tra storyboard
def test_valid_story_has_no_hard_errors():
    story, warns = st.validate(good_story())
    assert len(story.scenes) == 3 and story.aspect_ratio == "9:16"
    assert any("khuyến nghị 420-550" in w for w in warns)     # 12 từ, quá ngắn -> chỉ cảnh báo


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(aspect_ratio="16:9"), "9:16"),
    (lambda d: d["scenes"][0].update(motion="spin"), "motion"),
    (lambda d: d["scenes"][0].update(sfx="explode"), "sfx"),
    (lambda d: d["scenes"][1].update(id=1), "trùng"),
    (lambda d: d["scenes"][0].update(speech=""), "speech"),
    (lambda d: d["scenes"][0].update(image_prompt=""), "image_prompt"),
    (lambda d: d.pop("scenes"), "scenes"),
    (lambda d: d.update(scenes=[]), "không rỗng"),
])
def test_invalid_story_raises_clear_error(mutate, needle):
    data = good_story()
    mutate(data)
    with pytest.raises(StoryError, match=needle):
        st.validate(data)


def test_invalid_json_file_reports_location(tmp_path):
    p = tmp_path / "storyboard.json"
    p.write_text("{ không phải json", encoding="utf-8")
    with pytest.raises(StoryError, match="không phải JSON hợp lệ"):
        st.load_storyboard(p)


def test_storyboard_with_bom_is_accepted(tmp_path):
    p = tmp_path / "storyboard.json"
    p.write_text(json.dumps(good_story(), ensure_ascii=False), encoding="utf-8-sig")
    story, _ = st.load_storyboard(p)
    assert story.title == "Thử nghiệm"


# ================================================================ chuyển động & timeline
def test_ken_burns_expressions_per_motion():
    assert "1+0.25*on/10" in st.ken_burns("zoom_in", 10)
    assert "1.25-0.25*on/10" in st.ken_burns("zoom_out", 10)
    assert "(iw-iw/zoom)*(1-on/10)" in st.ken_burns("pan_left", 10)
    assert "(iw-iw/zoom)*on/10" in st.ken_burns("pan_right", 10)
    assert "z='1'" in st.ken_burns("static", 10)
    assert "s=1080x1920" in st.ken_burns("zoom_in", 10)


def test_timeline_is_contiguous():
    tl = st.plan_timeline([2.0, 3.0, 1.5])
    assert tl == [(0.0, 2.0), (2.0, 3.0), (5.0, 1.5)]


def test_atempo_chain_matches_speed():
    assert st.atempo_chain(1.15) == "atempo=1.15000"
    assert st.atempo_chain(2.5).startswith("atempo=2.0,")


# ================================================================ phụ đề karaoke
def test_chunks_have_at_most_five_words():
    text = " ".join(f"w{i}" for i in range(12))
    chunks = st.chunk_words(text)
    assert [len(c) for c in chunks] == [5, 5, 2]


def test_karaoke_events_cover_scene_duration_without_gaps():
    ev = st.karaoke_events("một hai ba bốn năm sáu bảy", start=10.0, duration=4.0)
    assert ev[0][0] == pytest.approx(10.0) and ev[-1][1] == pytest.approx(14.0)
    for a, b in zip(ev, ev[1:]):
        assert a[1] == pytest.approx(b[0])


def test_ass_contains_karaoke_tags_and_vietnamese_utf8(tmp_path):
    sc = st.Scene(1, "Xin chào các bạn, đây là Alan Turing.", "x", on_screen_text="NĂM 1950")
    ass = st.build_ass([sc], [(0.0, 5.0)], [4.5])
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "\\kf" in ass and "Title" in ass and "NĂM 1950" in ass
    out = tmp_path / "subs.ass"
    out.write_text(ass, encoding="utf-8")
    assert "chào" in out.read_text(encoding="utf-8")


def test_ass_escapes_braces_from_speech():
    sc = st.Scene(1, "giá {x} là 5", "x")
    ass = st.build_ass([sc], [(0.0, 3.0)], [2.5])
    assert "{x}" not in ass.split("[Events]")[1]


def test_ass_time_format():
    assert st.ass_time(0) == "0:00:00.00"
    assert st.ass_time(3661.5) == "1:01:01.50"


# ================================================================ TTS cache
def test_cache_key_ignores_speed_but_changes_with_text_and_voice():
    k1 = st.speech_cache_key("xin chào", "edge", "v1")
    assert k1 == st.speech_cache_key("xin chào", "edge", "v1")
    assert k1 != st.speech_cache_key("xin chào!", "edge", "v1")
    assert k1 != st.speech_cache_key("xin chào", "edge", "v2")


def test_cache_hit_avoids_synthesis_and_edit_regenerates_one(tmp_path):
    calls = []

    def synth(text, path):
        calls.append(text)
        st.synth_tone(text, path)

    cache = tmp_path / "cache"
    st.cached_speech("câu một", "tone", "x", synth, cache)
    st.cached_speech("câu một", "tone", "x", synth, cache)
    assert calls == ["câu một"]                       # lần 2 lấy từ cache
    st.cached_speech("câu hai", "tone", "x", synth, cache)
    assert calls == ["câu một", "câu hai"]            # sửa/thêm 1 câu chỉ tổng hợp đúng 1 lần


def test_corrupt_cache_entry_is_not_reused(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    key = st.speech_cache_key("abc", "tone", "x")
    (cache / f"{key}.wav").write_bytes(b"")          # file rỗng do lần trước bị ngắt
    calls = []

    def synth(text, path):
        calls.append(1)
        st.synth_tone(text, path)

    st.cached_speech("abc", "tone", "x", synth, cache)
    assert calls == [1]


def test_unknown_engine_is_rejected():
    with pytest.raises(StoryError, match="engine"):
        st.make_synth("vbee", "x")


# ================================================================ dựng phim end-to-end
def probe_streams(path):
    text = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stderr
    return {"video": "Video:" in text, "audio": "Audio:" in text, "duration": st.media_duration(path),
            "size": Path(path).stat().st_size, "text": text}


def test_build_end_to_end_with_mixed_assets(tmp_path):
    data = good_story(n=3, words_each=6)
    data["scenes"][1]["sfx"] = "pop"
    proj = make_project(tmp_path, data, mp4_scene=2)
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    st.write_wav(sfx_dir / "pop.wav", np.sin(np.linspace(0, 200, 4800)).astype(np.float32) * 0.5)
    out = tmp_path / "out" / "demo.mp4"
    logs = []
    opts = st.BuildOptions(engine="tone", sfx_dir=sfx_dir, cache_dir=tmp_path / "cache")
    st.build(proj, out, opts, log=logs.append)

    info = probe_streams(out)
    assert info["video"] and info["audio"]
    assert any("Tổng thời lượng" in m for m in logs)
    assert 3.0 < info["duration"] < 60.0
    assert info["size"] < 25 * 1024 * 1024


def test_build_without_subtitles_still_produces_video(tmp_path):
    proj = make_project(tmp_path, good_story(n=2, words_each=3))
    out = tmp_path / "o.mp4"
    st.build(proj, out, st.BuildOptions(engine="tone", cache_dir=tmp_path / "c", burn_subtitles=False), log=lambda m: None)
    assert probe_streams(out)["video"]


def test_missing_visual_lists_scene_id(tmp_path):
    proj = make_project(tmp_path, good_story(n=3))
    (proj / "assets" / "002.png").unlink()
    with pytest.raises(StoryError, match="cảnh 2"):
        st.build(proj, tmp_path / "o.mp4", st.BuildOptions(engine="tone", cache_dir=tmp_path / "c"), log=lambda m: None)


def test_audio_override_replaces_tts(tmp_path):
    proj = make_project(tmp_path, good_story(n=2, words_each=3))
    (proj / "assets" / "audio").mkdir(parents=True)
    st.write_wav(proj / "assets" / "audio" / "001.wav", np.full(24000, 0.2, dtype=np.float32))  # 1s giọng có sẵn

    def boom(text, path):
        raise AssertionError("không được gọi TTS cho cảnh đã có file audio")

    st.build(proj, tmp_path / "o.mp4", st.BuildOptions(engine="tone", cache_dir=tmp_path / "c", burn_subtitles=False),
             log=lambda m: None)
    assert probe_streams(tmp_path / "o.mp4")["video"]


def test_cli_validate_returns_error_code_on_bad_file(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"title": "x"}), encoding="utf-8")
    assert st.main(["validate", str(p)]) == 1
    assert "thiếu trường" in capsys.readouterr().out


def test_voice_track_starts_exactly_at_each_scene(tmp_path):
    """Giọng đọc mỗi cảnh phải nằm đúng mốc bắt đầu cảnh (không lệch, không đè nhau)."""
    data = good_story(n=3, words_each=6)
    proj = make_project(tmp_path, data)
    out = tmp_path / "o.mp4"
    st.build(proj, out, st.BuildOptions(engine="tone", cache_dir=tmp_path / "c", burn_subtitles=False,
                                        keep_temp=True), log=lambda m: None)
    voice = st.read_wav(tmp_path / "o_work" / "voice.wav")
    speech = [st.read_wav(p) for p in sorted((tmp_path / "o_work").glob("speech_*.wav"))]
    assert len(speech) == 3
    t = 0.0
    for x in speech:
        i = int(round(t * st.SR))
        head = voice[i:i + int(0.05 * st.SR)]
        assert np.abs(head).max() > 0.05, f"không có giọng tại mốc {t:.2f}s"
        before = voice[max(0, i - int(0.15 * st.SR)):max(0, i - int(0.05 * st.SR))]
        if before.size:
            assert np.abs(before).max() < 0.02, f"có tiếng trước mốc {t:.2f}s (lệch)"
        t += len(x) / st.SR + st.TAIL_PAD


def test_bgm_path_resolves_from_project_or_repo_root(tmp_path):
    repo = tmp_path / "repo"
    proj = repo / "projects" / "ep01"
    (proj).mkdir(parents=True)
    (repo / "assets_global" / "bgm").mkdir(parents=True)
    st.write_wav(repo / "assets_global" / "bgm" / "mystery.wav", np.zeros(2400, dtype=np.float32))
    st.write_wav(proj / "local.wav", np.zeros(2400, dtype=np.float32))
    assert st.resolve_bgm("assets_global/bgm/mystery.wav", proj, repo) == repo / "assets_global" / "bgm" / "mystery.wav"
    assert st.resolve_bgm("local.wav", proj, repo) == proj / "local.wav"
    assert st.resolve_bgm("khong_co.mp3", proj, repo) is None
    assert st.resolve_bgm("", proj, repo) is None


def test_build_with_bgm_and_sfx_mixes_all_tracks(tmp_path):
    """Nhánh trộn có BGM (sidechain ducking) + SFX phải chạy được và xuất đủ âm thanh."""
    data = good_story(n=3, words_each=5)
    data["bgm"] = "bgm.wav"
    data["scenes"][0]["sfx"] = "boom"
    proj = make_project(tmp_path, data)
    st.write_wav(proj / "bgm.wav", (0.3 * np.sin(np.linspace(0, 300, 24000 * 12))).astype(np.float32))
    sfx = tmp_path / "sfx"
    sfx.mkdir()
    st.write_wav(sfx / "boom.wav", np.full(4800, 0.5, dtype=np.float32))
    out = tmp_path / "o.mp4"
    logs = []
    st.build(proj, out, st.BuildOptions(engine="tone", cache_dir=tmp_path / "c", sfx_dir=sfx, keep_temp=True),
             log=logs.append)
    assert probe_streams(out)["audio"]
    mix = st.read_wav(tmp_path / "o_work" / "mix.wav")
    assert np.abs(mix).max() > 0.05
