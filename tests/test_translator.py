import json

from core.reporter import Reporter
from core.subtitles import Segment
from core.translator import GeminiTranslator, resolve_model


class _NotFound(Exception):
    code = 404


class _Forbidden(Exception):
    code = 403


def test_recovers_from_dead_model_and_missing_lines():
    calls = []

    def fake(system, user, model):
        calls.append(model)
        if model == "gemini-3.5-flash":
            raise _NotFound("models/gemini-3.5-flash is not found")
        lines = json.loads(user)["lines"]
        items = [{"id": l["id"], "text": "VI:" + l["text"]} for l in lines]
        if len(lines) > 2:
            items = items[:-1]  # cố tình làm thiếu 1 câu
        return "```json\n" + json.dumps(items, ensure_ascii=False) + "\n```"

    tr = GeminiTranslator(model="gemini-3.5-flash", generate_fn=fake, sleep=lambda s: None)
    segs = [Segment(i, i + 1, f"line {i}") for i in range(5)]
    out = tr.translate(segs, "en", "vi", batch_size=3, reporter=Reporter(on_log=lambda m, l: None))
    assert [s.text for s in out] == [f"VI:line {i}" for i in range(5)]
    assert "gemini-3.5-flash" not in calls[1:]  # model chết không bị gọi lại


def test_pronoun_rules_reach_prompt_and_payload():
    seen = {}

    def fake(system, user, model):
        seen["system"], seen["user"] = system, user
        lines = json.loads(user)["lines"]
        return json.dumps([{"id": l["id"], "text": "x"} for l in lines])

    tr = GeminiTranslator(model="gemini-3.5-flash", generate_fn=fake, sleep=lambda s: None)
    segs = [Segment(0, 1, "hello")]
    tr.translate(segs, "en", "vi", pronoun_rules="Tom gọi Jerry là 'mày'",
                 reporter=Reporter(on_log=lambda m, l: None))
    assert "pronoun" in seen["system"].lower()
    assert "Tom gọi Jerry" in seen["user"]


def test_resolve_model_picks_newest_matching_rule():
    available = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview",
                 "gemini-2.5-pro", "gemini-3.5-flash-image"]
    assert resolve_model("Nhanh (lời thoại ngắn)", available) == "gemini-3.5-flash-lite"
    assert resolve_model("Cân bằng (hằng ngày)", available) == "gemini-3.5-flash"
    assert resolve_model("Phức tạp (kịch bản dài)", available) == "gemini-3.1-pro-preview"


def test_resolve_model_none_when_nothing_matches():
    assert resolve_model("Phức tạp (kịch bản dài)", ["gemini-3.5-flash"]) is None


def test_403_permission_error_falls_back_to_next_model():
    calls = []

    def fake(system, user, model):
        calls.append(model)
        if model == "gemini-3.5-flash":
            raise _Forbidden("403 PERMISSION_DENIED: model not allowed for this key")
        lines = json.loads(user)["lines"]
        return json.dumps([{"id": l["id"], "text": "ok"} for l in lines])

    tr = GeminiTranslator(model="gemini-3.5-flash", generate_fn=fake, sleep=lambda s: None)
    segs = [Segment(0, 1, "hello")]
    out = tr.translate(segs, "en", "vi", reporter=Reporter(on_log=lambda m, l: None))

    assert out[0].text == "ok"
    assert calls[0] == "gemini-3.5-flash"
    assert "gemini-3.5-flash" not in calls[1:]  # model bị 403 không bị gọi lại
    assert tr.model != "gemini-3.5-flash"


def test_all_models_forbidden_raises_dubber_error():
    import pytest
    from core.errors import DubberError

    def fake(system, user, model):
        raise _Forbidden("403 forbidden")

    tr = GeminiTranslator(model="gemini-3.5-flash", generate_fn=fake, sleep=lambda s: None)
    with pytest.raises(DubberError):
        tr.translate([Segment(0, 1, "hello")], "en", "vi", reporter=Reporter(on_log=lambda m, l: None))


