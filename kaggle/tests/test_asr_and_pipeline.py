import copy
import json
import re
import sys
import types
from pathlib import Path

import pytest

import dubkaggle as dk
from dubkaggle import Config, Seg, Word


# ---------------------------------------------------------------- ASR gia lap
class FakeWord:
    def __init__(self, w, s, e):
        self.word, self.start, self.end, self.probability = w, s, e, 0.9


class FakeSegment:
    def __init__(self, s, e, text):
        self.start, self.end, self.text = s, e, text
        self.avg_logprob, self.no_speech_prob, self.compression_ratio = -0.3, 0.02, 1.1
        self.words = [FakeWord(" " + c, s + i * 0.1, s + i * 0.1 + 0.09) for i, c in enumerate(text)]


class FakeInfo:
    language, language_probability, duration = "zh", 0.99, 10.0


def install_fake_fw(monkeypatch, fail_on="", reject_hotwords=False):
    created = []

    class FakeModel:
        def __init__(self, size, device, compute_type):
            self.size, self.device, self.compute_type, self.kw = size, device, compute_type, None
            if fail_on == "load_cuda" and device == "cuda":
                raise RuntimeError("CUDA driver not found")
            created.append(self)

        def transcribe(self, audio, **kw):
            self.kw = kw
            if reject_hotwords and "hotwords" in kw:
                raise TypeError("transcribe() got an unexpected keyword argument 'hotwords'")
            device = self.device

            def gen():
                if fail_on == "iterate_cuda" and device == "cuda":
                    raise RuntimeError("Library libcudnn_ops.so.9 is not found")
                yield FakeSegment(1.0, 2.0, "你好世界")

            return gen(), FakeInfo()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    return created


def test_whisper_asr_passes_expected_arguments(monkeypatch):
    created = install_fake_fw(monkeypatch)
    cfg = Config(language="zh", hotwords="哆啦A梦", initial_prompt="前言", vad_min_silence_ms=300)
    asr = dk.WhisperASR(cfg)
    segs = asr.transcribe("x.wav", offset=10.0)
    kw = created[0].kw
    assert kw["word_timestamps"] is True and kw["language"] == "zh" and kw["vad_filter"] is True
    assert kw["vad_parameters"] == {"min_silence_duration_ms": 300}
    assert kw["hotwords"] == "哆啦A梦" and kw["condition_on_previous_text"] is False
    assert segs[0].start == 11.0 and segs[0].words[0].word == "你"      # offset + strip khoang trang
    assert asr.info["language"] == "zh"


def test_whisper_asr_vad_off_omits_vad_parameters(monkeypatch):
    created = install_fake_fw(monkeypatch)
    asr = dk.WhisperASR(Config())
    asr.transcribe("x.wav", vad=False)
    assert created[0].kw["vad_filter"] is False and "vad_parameters" not in created[0].kw


def test_whisper_asr_auto_language_passes_none(monkeypatch):
    created = install_fake_fw(monkeypatch)
    dk.WhisperASR(Config(language="auto")).transcribe("x.wav")
    assert created[0].kw["language"] is None


def test_whisper_asr_retries_without_hotwords_on_old_version(monkeypatch):
    created = install_fake_fw(monkeypatch, reject_hotwords=True)
    segs = dk.WhisperASR(Config(hotwords="abc")).transcribe("x.wav")
    assert len(segs) == 1 and "hotwords" not in created[0].kw


def test_whisper_asr_falls_back_when_cuda_load_fails(monkeypatch):
    created = install_fake_fw(monkeypatch, fail_on="load_cuda")
    asr = dk.WhisperASR(Config())
    asr.transcribe("x.wav")
    assert (asr.device, asr.compute_type) == ("cpu", "int8")


def test_whisper_asr_falls_back_when_cudnn_fails_during_iteration(monkeypatch):
    created = install_fake_fw(monkeypatch, fail_on="iterate_cuda")
    asr = dk.WhisperASR(Config())
    segs = asr.transcribe("x.wav")
    assert asr.device == "cpu" and len(segs) == 1                       # da ha cap va chay lai thanh cong


# ------------------------------------------------------------- quet lai khoang trong
class FakeASR:
    """Lan chinh tra ve `main`; lan quet khoang trong (vad=False) tra ve loi an theo thoi gian tuyet doi."""

    def __init__(self, main, hidden=(), info=None):
        self.main, self.hidden = main, list(hidden)
        self.info = info or {"language": "zh", "language_probability": 0.99}
        self.calls = []

    def transcribe(self, audio_path, offset=0.0, vad=None):
        self.calls.append((round(offset, 2), vad))
        if vad is False:
            dur = dk.probe_duration(audio_path)
            return [copy.deepcopy(s) for s in self.hidden if offset <= (s.start + s.end) / 2 <= offset + dur]
        return copy.deepcopy(self.main)


def test_recheck_gaps_recovers_hidden_speech(sample_video, tmp_path):
    audio = dk.extract_audio(sample_video, tmp_path / "a.wav")
    hidden = [Seg(12.0, 14.0, "被漏掉的一句话", avg_logprob=-0.5, no_speech_prob=0.1),
              Seg(20.0, 21.0, "字幕由Amara.org社区提供", avg_logprob=-0.4, no_speech_prob=0.1)]  # cau bia -> bi loai
    asr = FakeASR([], hidden)
    got = dk.recheck_gaps(asr, audio, [(10.0, 25.0)], Config(), tmp_path, total=30.0)
    assert [s.text for s in got] == ["被漏掉的一句话"] and "recovered" in got[0].flags
    assert asr.calls == [(9.75, False)]                                    # co pad 0.25s, VAD tat


def test_cut_audio_has_expected_length(sample_video, tmp_path):
    audio = dk.extract_audio(sample_video, tmp_path / "a.wav")
    clip = dk.cut_audio(audio, tmp_path / "c.wav", 5.0, 8.0)
    assert abs(dk.probe_duration(clip) - 3.0) < 0.1


# ----------------------------------------------------------- pipeline giai doan 1
def build_main():
    def s(a, b, t, lp=-0.3, ns=0.02):
        return Seg(a, b, t, [Word(c, a + i * (b - a) / len(t), a + (i + 1) * (b - a) / len(t)) for i, c in enumerate(t)],
                   avg_logprob=lp, no_speech_prob=ns, compression_ratio=1.1)

    return [
        s(0.5, 0.9, "你好"), s(1.0, 1.5, "世界"),                    # ngan -> gop
        s(2.0, 3.0, "字幕由Amara.org社区提供", lp=-1.5, ns=0.8),      # bia -> bo
        s(4.0, 5.0, "好的好的。"), s(5.1, 6.0, "好的好的。"), s(6.1, 7.0, "好的好的。"), s(7.1, 8.0, "好的好的。"),  # lap
        s(9.0, 21.0, "这是一句非常非常长的话需要被拆成几个更短的片段。" + "后面还有很多内容要说。" * 2),   # dai -> tach
        s(27.0, 29.0, "最后一句话", lp=-1.4),                          # low_conf
    ]


def test_run_transcribe_end_to_end(sample_video, tmp_path):
    cfg = Config(video_path=str(sample_video), work_dir=str(tmp_path / "w"), out_dir=str(tmp_path / "out"))
    # Loi an nam o khoang trong 22-26s (giua cau dai ket thuc luc 21s va cau cuoi luc 27s)
    hidden = [Seg(23.0, 25.0, "漏掉的话", avg_logprob=-0.4, no_speech_prob=0.1)]
    asr = FakeASR(build_main(), hidden)
    res = dk.run_transcribe(cfg, asr=asr)

    texts = [s.text for s in res["segments"]]
    assert "你好世界" in texts                                            # da gop
    assert not any("Amara" in t for t in texts)                          # da bo cau bia
    assert sum(t == "好的好的。" for t in texts) == 2                      # lap chi giu 2
    assert "漏掉的话" in texts                                            # da vot lai tu khoang trong
    assert all(s.duration <= cfg.max_seg_seconds + 1e-6 for s in res["segments"])
    assert [s.id for s in res["segments"]] == list(range(1, len(texts) + 1))

    qc = res["qc"]
    assert qc["dropped_hallucinations"] >= 1 and qc["flag_counts"].get("low_conf") == 1
    assert qc["flag_counts"].get("recovered") == 1
    for key in ("transcript_json", "srt", "script", "qc_json", "qc_md", "bundle"):
        assert Path(res["paths"][key]).exists(), key
    recovered = [x for x in res["segments"] if "recovered" in x.flags]
    assert len(recovered) == 1 and 22.0 < recovered[0].start < 26.0
    assert asr.calls[0][1] is None                                  # lan chinh dung cau hinh VAD mac dinh
    assert any(vad is False for _, vad in asr.calls[1:])           # lan quet lai khoang trong tat VAD


def test_script_output_is_parseable_by_dubstudio_format(sample_video, tmp_path):
    cfg = Config(video_path=str(sample_video), work_dir=str(tmp_path / "w"), out_dir=str(tmp_path / "out"))
    res = dk.run_transcribe(cfg, asr=FakeASR(build_main()))
    line = re.compile(r"^\[(\d\d:\d\d:\d\d\.\d{3}) -> (\d\d:\d\d:\d\d\.\d{3})\] (.+)$")
    lines = Path(res["paths"]["script"]).read_text(encoding="utf-8").strip().splitlines()
    assert lines and all(line.match(l) for l in lines)
    srt = Path(res["paths"]["srt"]).read_text(encoding="utf-8")
    assert re.search(r"^1\n\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}\n", srt)


def test_transcript_json_roundtrip_keeps_words(sample_video, tmp_path):
    cfg = Config(video_path=str(sample_video), work_dir=str(tmp_path / "w"), out_dir=str(tmp_path / "out"))
    res = dk.run_transcribe(cfg, asr=FakeASR(build_main()))
    segs, source = dk.load_transcript(res["paths"]["transcript_json"])
    assert source["language"] == "zh" and abs(source["duration"] - 30.0) < 0.5
    assert len(segs) == len(res["segments"])
    assert segs[0].text == res["segments"][0].text and len(segs[0].words) > 0
    assert segs[0].words[0].end > segs[0].words[0].start
    data = json.loads(Path(res["paths"]["transcript_json"]).read_text(encoding="utf-8"))
    assert data["schema"] == dk.SCHEMA and "video_path" not in data["settings"]   # khong lo duong dan


def test_load_transcript_rejects_wrong_schema(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"schema": "other", "segments": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        dk.load_transcript(p)


def test_run_transcribe_rejects_missing_and_silent_video(silent_video, tmp_path):
    with pytest.raises(FileNotFoundError):
        dk.run_transcribe(Config(video_path=str(tmp_path / "none.mp4"), work_dir=str(tmp_path), out_dir=str(tmp_path)))
    with pytest.raises(RuntimeError, match="âm thanh"):
        dk.run_transcribe(Config(video_path=str(silent_video), work_dir=str(tmp_path / "w"), out_dir=str(tmp_path / "o")),
                          asr=FakeASR([]))


def test_qc_markdown_lists_flags_and_gaps():
    segs = [Seg(0, 2, "一句话", avg_logprob=-1.5), Seg(20, 22, "又一句")]
    segs[0].id, segs[1].id = 1, 2
    dk.annotate_flags(segs, Config(), "zh")
    qc = dk.build_qc(segs, 40.0, Config(), "zh", dropped=3)
    md = dk.qc_markdown(qc)
    assert "low_conf" in md and "Khoảng trống dài" in md and "Câu 'bịa' đã bỏ: 3" in md
    assert qc["coverage"] == round(4 / 40, 3)


def test_fast_speech_flag_uses_language_limit():
    s = Seg(0, 1, "这是一句说得非常非常快的话语啊")   # 14 ky tu / 1s
    dk.annotate_flags([s], Config(), "zh")
    assert "fast_speech" in s.flags


def test_bundle_contains_all_outputs(sample_video, tmp_path):
    import zipfile
    cfg = Config(video_path=str(sample_video), work_dir=str(tmp_path / "w"), out_dir=str(tmp_path / "out"))
    res = dk.run_transcribe(cfg, asr=FakeASR(build_main()))
    names = set(zipfile.ZipFile(res["paths"]["bundle"]).namelist())
    assert {"transcript.json", "transcript.srt", "script.txt", "qc_report.json", "qc_report.md"} <= names
