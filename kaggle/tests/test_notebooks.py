"""Chạy thật từng ô code của hai notebook (giả lập thư viện nặng) để bắt lỗi tên biến, import, đường dẫn."""
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_notebooks as bn  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("nb")
    return {p.name: p for p in bn.build(out)}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def code_cells(nb):
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_notebooks_are_valid_nbformat_json(built):
    for name, path in built.items():
        nb = load(path)
        assert nb["nbformat"] == 4 and nb["cells"], name
        for c in nb["cells"]:
            assert c["cell_type"] in ("markdown", "code")
            assert isinstance(c["source"], list)
            if c["cell_type"] == "code":
                assert c["outputs"] == [] and c["execution_count"] is None   # không kèm kết quả cũ


def test_every_code_cell_compiles(built):
    for name, path in built.items():
        for i, src in enumerate(code_cells(load(path))):
            first = src.lstrip().splitlines()[0] if src.strip() else ""
            if first.startswith("%%writefile"):
                src = src.split("\n", 1)[1]
            elif first.startswith("%pip"):
                continue
            compile(src, f"{name}[cell {i}]", "exec")


def test_embedded_library_is_exactly_the_tested_source(built):
    lib = (ROOT / "dubkaggle.py").read_text(encoding="utf-8")
    for name, path in built.items():
        cell = next(s for s in code_cells(load(path)) if s.startswith("%%writefile dubkaggle.py"))
        assert cell.split("\n", 1)[1] == lib.rstrip("\n"), f"{name} không khớp dubkaggle.py (chạy lại tools/build_notebooks.py)"


def test_notebooks_contain_no_secrets_or_hardcoded_keys(built):
    for path in built.values():
        text = Path(path).read_text(encoding="utf-8")
        assert "AIza" not in text and "sk-" not in text and "api_key=\"" not in text


# ------------------------------------------------------------------ chạy thật từng ô
def run_cells(nb_path, cwd, monkeypatch):
    nb = load(nb_path)
    ns = {"__name__": "__main__"}
    monkeypatch.chdir(cwd)
    monkeypatch.syspath_prepend(str(cwd))
    monkeypatch.delitem(sys.modules, "dubkaggle", raising=False)     # buộc import bản do notebook ghi ra
    for i, src in enumerate(code_cells(nb)):
        first = src.lstrip().splitlines()[0] if src.strip() else ""
        if first.startswith("%%writefile"):
            (Path(cwd) / first.split()[1]).write_text(src.split("\n", 1)[1], encoding="utf-8")
            continue
        if first.startswith("%pip") or first.startswith("!"):
            continue
        try:
            exec(compile(src, f"<{Path(nb_path).name} cell {i}>", "exec"), ns)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"Ô code #{i} của {Path(nb_path).name} lỗi: {type(e).__name__}: {e}\n---\n{src[:400]}") from e
    return ns


def install_fake_whisper(monkeypatch):
    class W:
        def __init__(self, w, s, e):
            self.word, self.start, self.end, self.probability = w, s, e, 0.9

    class Seg:
        def __init__(self, s, e, text):
            self.start, self.end, self.text = s, e, text
            self.avg_logprob, self.no_speech_prob, self.compression_ratio = -0.3, 0.02, 1.1
            self.words = [W(c, s + i * 0.2, s + i * 0.2 + 0.18) for i, c in enumerate(text)]

    class Info:
        language, language_probability, duration = "zh", 0.99, 30.0

    class Model:
        def __init__(self, size, device, compute_type):
            pass

        def transcribe(self, audio, **kw):
            return iter([Seg(1.0, 2.0, "你好世界")]), Info()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))


def install_fake_genai(monkeypatch):
    from google import genai

    class Models:
        def list(self):
            ns = types.SimpleNamespace
            return [ns(name="models/gemini-3.5-flash", supported_actions=["generateContent"]),
                    ns(name="models/gemini-3.5-flash-image", supported_actions=["generateContent"]),
                    ns(name="models/text-embedding-004", supported_actions=["embedContent"])]

        def generate_content(self, model, contents, config):
            out = []
            for l in json.loads(contents)["lines"]:
                if "current" in l:
                    text = "Ngắn gọn"
                else:
                    text = ("Một câu dịch rất dài " * 4).strip() if l["id"] % 2 == 0 else "Xin chào"
                out.append({"id": l["id"], "text": text})
            return types.SimpleNamespace(text=json.dumps(out, ensure_ascii=False))

    class Client:
        def __init__(self, api_key=None):
            self.models = Models()

    monkeypatch.setattr(genai, "Client", Client)


def test_notebook_01_then_02_run_end_to_end(built, sample_video, tmp_path, monkeypatch):
    shutil.copy(sample_video, tmp_path / "episode01.mp4")            # notebook tự tìm video ở thư mục hiện tại
    install_fake_whisper(monkeypatch)

    ns1 = run_cells(built["01_transcribe.ipynb"], tmp_path, monkeypatch)
    out = tmp_path / "working" / "output"
    for name in ("transcript.json", "transcript.srt", "script.txt", "qc_report.md", "qc_report.json"):
        assert (out / name).exists(), name
    assert (tmp_path / "working" / "dubstudio_output.zip").exists()
    assert ns1["result"]["segments"] and ns1["cfg"].video_path.endswith("episode01.mp4")

    # ----- Notebook 02 đọc đúng file mà Notebook 01 vừa sinh ra
    install_fake_genai(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    ns2 = run_cells(built["02_translate_fit.ipynb"], tmp_path, monkeypatch)
    out2 = tmp_path / "working" / "output_translate"
    script = (out2 / "script_vi.txt").read_text(encoding="utf-8")
    assert "Ngắn gọn" in script and script.startswith("[00:00:")
    assert (out2 / "fit_report.md").exists() and (tmp_path / "working" / "dubstudio_translate_vi.zip").exists()
    assert ns2["res"]["before"]["too_fast"] >= 1 and ns2["res"]["after"]["too_fast"] == 0


def test_notebook_01_reports_clear_error_when_no_video(built, tmp_path, monkeypatch):
    install_fake_whisper(monkeypatch)
    with pytest.raises(AssertionError, match="Không thấy video|FileNotFoundError"):
        run_cells(built["01_transcribe.ipynb"], tmp_path, monkeypatch)


def test_committed_notebooks_are_up_to_date(built):
    """File notebooks/*.ipynb trong repo phải trùng với bản dựng mới nhất từ dubkaggle.py."""
    for name, fresh in built.items():
        committed = ROOT / "notebooks" / name
        assert committed.exists(), f"thiếu {committed}"
        assert load(committed) == load(fresh), f"{name} đã cũ so với dubkaggle.py: chạy `python tools/build_notebooks.py`"
