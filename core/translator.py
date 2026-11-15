"""Dịch kịch bản theo ngữ cảnh bằng Gemini (SDK `google-genai`), có dự phòng Google Translate miễn phí.

Khác bản cũ: yêu cầu model trả về JSON theo id (không bắt model tự viết SRT), kiểm tra đủ số câu,
tự dịch lại câu thiếu, tự đổi model khi tên model không còn tồn tại, và giới hạn độ dài câu theo
khung thời gian để giọng đọc khỏi phải tăng tốc quá nhiều.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, List, Optional

from .config import DEFAULT_GEMINI_MODELS
from .errors import DubberError, MissingDependency
from .languages import get_language
from .reporter import Reporter
from .subtitles import Segment

GenerateFn = Callable[[str, str, str], str]  # (system, user, model) -> text


def _system_prompt(src_name: str, tgt_name: str) -> str:
    return (
        f"You are a professional subtitle translator and dubbing-script adapter. "
        f"Translate the given lines from {src_name} to {tgt_name}.\n"
        "The result will be read aloud by a text-to-speech voice and must fit the time available.\n"
        "Rules:\n"
        "1. Output ONLY a JSON array of objects: [{\"id\": <int>, \"text\": <string>}, ...]. "
        "Same ids, same count, same order. Never merge, split, drop or add lines.\n"
        "2. Write natural SPOKEN language, not literal translation. Fix obvious speech-recognition errors.\n"
        "3. Respect each line's `max_chars` (soft limit): shorten by removing filler, never by dropping key meaning.\n"
        "4. Keep names, terminology and the way speakers address each other consistent across lines. "
        "Follow the glossary exactly when given.\n"
        "5. Write numbers and symbols the way they should be spoken aloud.\n"
        "6. `previous_lines` is context only, do not translate it again.\n"
        "7. If `pronoun_rules` is given, apply it to every line involving those speakers and keep it "
        "identical across the whole script. Do not switch pronouns between lines.\n"
    )


def _parse_json_map(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        data = json.loads(t)
    except ValueError:
        m = re.search(r"\[.*\]", t, re.S)
        if not m:
            raise DubberError("Model không trả về JSON hợp lệ")
        data = json.loads(m.group(0))
    if isinstance(data, dict):
        for k in ("translations", "lines", "items", "result"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    out = {}
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and "id" in d and "text" in d:
            try:
                out[int(d["id"])] = str(d["text"]).strip()
            except (TypeError, ValueError):
                pass
    return out


def _gtranslate_code(code: str) -> str:
    return {"zh": "zh-CN"}.get(code, code)


class FreeTranslator:
    """Dự phòng khi không có API key: Google Translate (deep-translator), dịch từng câu, không hiểu ngữ cảnh."""

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str,
                  reporter: Optional[Reporter] = None, **_) -> List[Segment]:
        reporter = reporter or Reporter()
        try:
            from deep_translator import GoogleTranslator
        except ImportError as e:
            raise MissingDependency("Thiếu deep-translator. Chạy: pip install deep-translator") from e
        tr = GoogleTranslator(source=_gtranslate_code(source_lang) if source_lang not in ("", "auto") else "auto",
                              target=_gtranslate_code(target_lang))
        out = []
        for i, s in enumerate(segments):
            reporter.check_cancel()
            src = s.src or s.text
            try:
                text = tr.translate(src) or src
            except Exception as e:  # mạng lỗi một câu thì giữ nguyên câu gốc, không làm hỏng cả job
                reporter.log(f"⚠️ Câu {i + 1} dịch lỗi ({e}); giữ nguyên bản gốc", "warn")
                text = src
            out.append(Segment(s.start, s.end, text, src))
            reporter.progress((i + 1) * 100 / len(segments), f"Dịch {i + 1}/{len(segments)}")
        return out


def list_gemini_models(api_key: str) -> List[str]:
    """Hỏi trực tiếp API xem hiện có model nào (tên model đổi liên tục)."""
    try:
        from google import genai
    except ImportError as e:
        raise MissingDependency("Thiếu google-genai. Chạy: pip install google-genai") from e
    client = genai.Client(api_key=api_key)
    names = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        name = (m.name or "").replace("models/", "")
        if "generateContent" in actions and name.startswith("gemini") and not re.search(
                r"image|tts|live|audio|embed|robotics|computer|transcribe", name):
            names.append(name)
    return sorted(set(names), reverse=True)


def resolve_model(profile_name: str, available: list) -> str | None:
    """Chọn model mới nhất khớp quy tắc của chế độ. Trả None nếu không có model nào khớp."""
    from core.style_presets import TASK_PROFILES

    rule = TASK_PROFILES.get(profile_name)
    if not rule:
        return None
    for name in sorted(available, reverse=True):  # tên có số phiên bản lớn hơn đứng trước
        if rule["include"] in name and not any(x in name for x in rule["exclude"]):
            return name
    return None


class GeminiTranslator:
    def __init__(self, api_key: str = "", model: str = DEFAULT_GEMINI_MODELS[0],
                 generate_fn: Optional[GenerateFn] = None, sleep: Callable[[float], None] = time.sleep):
        self.model = model
        self._generate = generate_fn or self._make_generate(api_key)
        self._sleep = sleep
        self._dead: set = set()
        self.reporter = Reporter()

    @staticmethod
    def _make_generate(api_key: str) -> GenerateFn:
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise MissingDependency("Thiếu google-genai. Chạy: pip install google-genai") from e
        if not api_key:
            raise DubberError("Chưa có GEMINI_API_KEY (điền trong tab Cài đặt hoặc file .env)")
        client = genai.Client(api_key=api_key)

        def generate(system: str, user: str, model: str) -> str:
            resp = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system, response_mime_type="application/json", temperature=0.4),
            )
            if not resp.text:
                raise DubberError("Model trả về rỗng (có thể bị bộ lọc an toàn chặn)")
            return resp.text

        return generate

    # ------------------------------------------------------------ gọi model
    def _models_chain(self) -> List[str]:
        chain = [self.model] + [m for m in DEFAULT_GEMINI_MODELS if m != self.model]
        return [m for m in chain if m not in self._dead] or chain

    def _call(self, system: str, user: str) -> str:
        last: Optional[Exception] = None
        for model in self._models_chain():
            for attempt in range(4):
                self.reporter.check_cancel()
                try:
                    out = self._generate(system, user, model)
                    if model != self.model:
                        self.reporter.log(f"ℹ️ Đang dùng model dự phòng: {model}", "warn")
                        self.model = model
                    return out
                except Exception as e:
                    last = e
                    code = getattr(e, "code", None)
                    msg = str(e).lower()
                    if code in (403, 404) or any(s in msg for s in (
                            "not found", "no longer available", "not supported", "permission", "forbidden")):
                        self.reporter.log(f"⚠️ Model '{model}' không dùng được với key này, thử model khác...", "warn")
                        self._dead.add(model)
                        break
                    if code in (429, 500, 502, 503, 504) or "quota" in msg or "overloaded" in msg or "unavailable" in msg:
                        wait = 2 ** (attempt + 1)
                        self.reporter.log(f"⏳ API bận/giới hạn, chờ {wait}s rồi thử lại...", "warn")
                        self._sleep(wait)
                        continue
                    if isinstance(e, DubberError) and attempt < 1:
                        continue
                    raise
        raise DubberError(f"Gọi Gemini thất bại sau nhiều lần thử: {last}")

    # ----------------------------------------------------------------- dịch
    def translate(self, segments: List[Segment], source_lang: str, target_lang: str, *,
                  topic: str = "", style: str = "", glossary: str = "", pronoun_rules: str = "",
                  batch_size: int = 40, reporter: Optional[Reporter] = None) -> List[Segment]:
        self.reporter = reporter or Reporter()
        tgt = get_language(target_lang)
        src_name = "the source language (auto-detect)" if source_lang in ("", "auto") else get_language(source_lang).english
        system = _system_prompt(src_name, tgt.english)

        n = len(segments)
        result: List[Segment] = [Segment(s.start, s.end, "", s.src or s.text) for s in segments]
        prev_ctx: List[str] = []
        for b0 in range(0, n, batch_size):
            self.reporter.check_cancel()
            idx = list(range(b0, min(n, b0 + batch_size)))
            got = self._translate_batch(system, segments, idx, tgt.cps, topic, style, glossary, prev_ctx, pronoun_rules)
            missing = [i for i in idx if not got.get(i)]
            if missing:
                self.reporter.log(f"⚠️ Thiếu {len(missing)} câu, dịch lại riêng...", "warn")
                got.update(self._translate_batch(system, segments, missing, tgt.cps, topic, style, glossary, prev_ctx, pronoun_rules))
            for i in idx:
                text = got.get(i)
                if not text:
                    text = self._fallback_one(result[i].src, source_lang, target_lang)
                result[i].text = text
            prev_ctx = [result[i].text for i in idx[-4:]]
            self.reporter.progress(min(idx[-1] + 1, n) * 100 / n, f"Dịch {min(idx[-1] + 1, n)}/{n} câu")
        return result

    def _translate_batch(self, system, segments, idx, cps, topic, style, glossary, prev_ctx,
                         pronoun_rules: str = "") -> dict:
        payload = {
            "topic": topic or "(not specified)",
            "style": style or "natural spoken language",
            "glossary": glossary or "(none)",
            "pronoun_rules": pronoun_rules or "(not specified: use natural pronouns)",
            "previous_lines": prev_ctx,
            "lines": [
                {"id": i, "text": segments[i].src or segments[i].text,
                 "max_chars": max(8, round(segments[i].duration * cps * 1.15))}
                for i in idx
            ],
        }
        try:
            raw = self._call(system, json.dumps(payload, ensure_ascii=False))
            return {i: t for i, t in _parse_json_map(raw).items() if i in set(idx)}
        except DubberError as e:
            self.reporter.log(f"⚠️ Lỗi khi dịch một đợt: {e}", "warn")
            return {}

    def _fallback_one(self, text: str, source_lang: str, target_lang: str) -> str:
        try:
            from deep_translator import GoogleTranslator
            return GoogleTranslator(source="auto", target=_gtranslate_code(target_lang)).translate(text) or text
        except Exception:
            return text
