import json
from pathlib import Path

import pytest

import dubkaggle as dk
from dubkaggle import Seg, TranslateConfig, Translator


class HttpError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg or f"HTTP {code}")
        self.code = code


def make_segs(n=6, dur=2.0, gap=0.5):
    return [Seg(i * (dur + gap), i * (dur + gap) + dur, f"原文{i}", id=i + 1) for i in range(n)]


def echo_llm(prefix="VI:", drop_last_if_gt=None, calls=None):
    def fake(system, user, model):
        if calls is not None:
            calls.append((model, user))
        lines = json.loads(user)["lines"]
        items = [{"id": l["id"], "text": f"{prefix}{l.get('text', l.get('current', ''))}"} for l in lines]
        if drop_last_if_gt and len(lines) > drop_last_if_gt:
            items = items[:-1]
        return "```json\n" + json.dumps(items, ensure_ascii=False) + "\n```"
    return fake


def test_translate_recovers_missing_lines_and_keeps_order():
    segs = make_segs(5)
    tr = Translator(TranslateConfig(batch_size=3), echo_llm(drop_last_if_gt=2), sleep=lambda s: None, logger=lambda m: None)
    out = tr.translate(segs, total=20.0)
    assert [s.text for s in out] == [f"VI:原文{i}" for i in range(5)]
    assert out[0].src == "原文0" and not any("untranslated" in s.flags for s in out)


def test_translate_sends_glossary_pronouns_and_context():
    calls = []
    t = TranslateConfig(glossary="大雄=Nobita", pronoun_rules="Nobita goi Doraemon la 'Doraemon'", batch_size=2)
    Translator(t, echo_llm(calls=calls), sleep=lambda s: None, logger=lambda m: None).translate(make_segs(4), 20.0)
    first, second = json.loads(calls[0][1]), json.loads(calls[1][1])
    assert first["glossary"] == "大雄=Nobita" and "Doraemon" in first["pronoun_rules"]
    assert first["previous_lines"] == [] and second["previous_lines"] == ["VI:原文0", "VI:原文1"]
    assert all(l["max_chars"] >= 6 for l in first["lines"])


def test_translate_max_chars_borrows_gap_until_next_line():
    segs = [Seg(0, 1, "a", id=1), Seg(5, 6, "b", id=2)]       # cau 1 chi 1s nhung duoc muon den 4.95s
    calls = []
    Translator(TranslateConfig(), echo_llm(calls=calls), sleep=lambda s: None, logger=lambda m: None).translate(segs, 6.0)
    lines = {l["id"]: l["max_chars"] for l in json.loads(calls[0][1])["lines"]}
    assert lines[0] > lines[1] * 3          # cau 1 muon duoc khoang lang, cau 2 (cuoi) thi khong


def test_dead_model_404_and_403_fall_through_to_next():
    seen = []

    def fake(system, user, model):
        seen.append(model)
        if model == "m-404":
            raise HttpError(404, "models/m-404 is not found")
        if model == "m-403":
            raise HttpError(403, "PERMISSION_DENIED forbidden")
        return json.dumps([{"id": l["id"], "text": "ok"} for l in json.loads(user)["lines"]])

    t = TranslateConfig(model="m-404", fallback_models=("m-403", "m-good"))
    out = Translator(t, fake, sleep=lambda s: None, logger=lambda m: None).translate(make_segs(2), 10.0)
    assert [s.text for s in out] == ["ok", "ok"]
    assert seen.count("m-404") == 1 and seen.count("m-403") == 1                 # model chet khong bi goi lai


def test_rate_limit_retries_with_backoff_then_succeeds():
    attempts, waits = [], []

    def fake(system, user, model):
        attempts.append(1)
        if len(attempts) < 3:
            raise HttpError(429, "quota")
        return json.dumps([{"id": l["id"], "text": "ok"} for l in json.loads(user)["lines"]])

    out = Translator(TranslateConfig(), fake, sleep=waits.append, logger=lambda m: None).translate(make_segs(1), 10.0)
    assert out[0].text == "ok" and waits == [2, 4]


def test_all_models_forbidden_marks_lines_untranslated_instead_of_crashing():
    def fake(system, user, model):
        raise HttpError(403, "forbidden")

    out = Translator(TranslateConfig(), fake, sleep=lambda s: None, logger=lambda m: None).translate(make_segs(2), 10.0)
    assert [s.text for s in out] == ["原文0", "原文1"] and all("untranslated" in s.flags for s in out)


def test_fit_rows_status_thresholds_and_summary():
    segs = [Seg(0, 2, "x" * 20, id=1), Seg(2.1, 4.1, "y" * 34, id=2), Seg(4.2, 6.2, "z" * 60, id=3)]
    # total=6.25: cau cuoi khong co khoang lang de muon nen khung chi con 2.0s
    rows = dk.fit_rows(segs, 6.25, cps=15.0, cap=1.25)
    assert [r["status"] for r in rows] == ["ok", "tight", "too_fast"]
    # Ngược lại, neu video con dai thi cau cuoi muon duoc khoang lang phia sau va het qua nhanh
    assert dk.fit_rows(segs, 10.0, cps=15.0, cap=1.25)[2]["status"] == "ok"
    summ = dk.fit_summary(rows, 1.25)
    assert summ["too_fast"] == 1 and summ["extra_seconds_needed"] > 0
    assert summ["suggested_video_slowdown"] > 1.0


def test_suggest_video_slowdown_is_one_when_everything_fits():
    rows = [{"need_speed": 0.8}, {"need_speed": 1.0}]
    assert dk.suggest_video_slowdown(rows, 1.25) == 1.0


def test_calibrate_cps_measures_real_rate():
    assert dk.calibrate_cps(["abcde fghij", "klmno pqrst"], lambda t: 1.0) == 11.0
    with pytest.raises(ValueError):
        dk.calibrate_cps(["a"], lambda t: 0.0)


def test_shorten_only_accepts_shorter_nonempty_text():
    segs = [Seg(0, 1, "x" * 40, id=1), Seg(1.1, 2.1, "y" * 40, id=2), Seg(2.2, 6.2, "ok", id=3)]

    def fake(system, user, model):
        out = []
        for l in json.loads(user)["lines"]:
            if l["id"] == 0:
                out.append({"id": 0, "text": "ngan"})                  # chap nhan
            else:
                out.append({"id": l["id"], "text": "z" * 80})          # dai hon -> tu choi
        return json.dumps(out)

    tr = Translator(TranslateConfig(shorten_rounds=1), fake, sleep=lambda s: None, logger=lambda m: None)
    out, rows = tr.shorten(segs, total=10.0)
    assert out[0].text == "ngan" and "shortened" in out[0].flags
    assert out[1].text == "y" * 40 and "shortened" not in out[1].flags


def test_shorten_stops_after_configured_rounds():
    segs = [Seg(0, 1, "x" * 60, id=1), Seg(1.05, 6, "ok", id=2)]
    calls = []

    def fake(system, user, model):
        calls.append(1)
        return json.dumps([{"id": 0, "text": "x" * 59}])           # chi rut 1 ky tu moi vong

    Translator(TranslateConfig(shorten_rounds=2), fake, sleep=lambda s: None, logger=lambda m: None).shorten(segs, 10.0)
    assert len(calls) == 2


def test_run_translate_end_to_end_writes_dubstudio_readable_files(tmp_path):
    segs = [Seg(0, 2, "你好", id=1), Seg(2.1, 3.0, "这是一句很长的话需要被压缩", id=2), Seg(3.1, 6.0, "再见", id=3)]
    tpath = tmp_path / "transcript.json"
    dk.write_json(tpath, dk.to_dict(segs, {"duration": 10.0, "language": "zh", "file": "a.mp4"}))

    def fake(system, user, model):
        payload = json.loads(user)
        out = []
        for l in payload["lines"]:
            if "current" in l:
                out.append({"id": l["id"], "text": "Ngan gon"})
            else:
                out.append({"id": l["id"], "text": "Day la mot cau dich rat rat dai khong the doc kip" if l["id"] == 1 else "Xin chao"})
        return json.dumps(out)

    t = TranslateConfig(out_dir=str(tmp_path / "o"), speed_cap=1.25)
    res = dk.run_translate(tpath, t, fake, sleep=lambda s: None)
    assert res["before"]["too_fast"] >= 1 and res["after"]["too_fast"] < res["before"]["too_fast"]
    script = Path(res["paths"]["script"]).read_text(encoding="utf-8")
    assert "[00:00:02.100 -> 00:00:03.000] Ngan gon" in script
    assert Path(res["paths"]["fit_md"]).exists() and Path(res["paths"]["bundle"]).exists()
    back, src = dk.load_transcript(res["paths"]["json"])
    assert back[1].src == "这是一句很长的话需要被压缩" and src["language"] == "vi"


def test_slowdown_not_recommended_when_one_outlier_would_force_huge_slowdown():
    rows = [{"need_speed": 0.9}] * 6 + [{"need_speed": 2.5}]            # 1 câu quá nhanh trong 7
    summ = dk.fit_summary([{**r, "est_seconds": 1.0, "window": 1.0, "status": "ok"} for r in rows], 1.25)
    assert summ["suggested_video_slowdown"] == 2.0 and summ["slowdown_recommended"] is False
    md = dk.fit_markdown([{**r, "id": i, "index": i, "est_seconds": 1.0, "window": 1.0, "status": "ok", "max_chars": 9}
                          for i, r in enumerate(rows)], summ, [Seg(0, 1, "x")] * 7, 1.25)
    assert "không khuyến nghị" in md and "Gợi ý làm chậm video chung" not in md


def test_slowdown_recommended_when_modest():
    rows = [{"need_speed": 1.3, "est_seconds": 1, "window": 1, "status": "tight"}] * 5
    summ = dk.fit_summary(rows, 1.25)
    assert summ["slowdown_recommended"] is True and 1.0 < summ["suggested_video_slowdown"] <= 1.2
