"""Sinh 2 notebook Kaggle từ dubkaggle.py.

    python tools/build_notebooks.py

Notebook nhúng NGUYÊN VĂN dubkaggle.py qua `%%writefile`, nên code chạy trên Kaggle luôn trùng với
code đã được test bằng pytest. Muốn sửa logic thì sửa dubkaggle.py rồi chạy lại script này.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "dubkaggle.py"


def _lines(text: str):
    text = text.strip("\n")
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + [parts[-1]]


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": _lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _lines(text)}


def notebook(cells) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


def writefile_cell() -> dict:
    return code("%%writefile dubkaggle.py\n" + LIB.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- notebook 01
def nb01() -> dict:
    return notebook([
        md("""
# 🎙️ DubStudio · Bước 1: Video → Kịch bản

Notebook này nhận **một video**, nhận diện giọng nói bằng GPU của Kaggle rồi xuất:

| File | Dùng để |
|---|---|
| `script.txt` | Kịch bản dạng `[giờ -> giờ] nội dung`. DubStudio đọc trực tiếp (nút **📂 Mở...**) |
| `transcript.srt` | Phụ đề chuẩn SRT |
| `transcript.json` | Bản đầy đủ: **mốc từng từ**, độ tin cậy, cờ cảnh báo. Dùng cho Bước 2 |
| `qc_report.md` | Báo cáo kiểm tra: câu đáng ngờ, khoảng trống dài, câu nói quá nhanh |
| `dubstudio_output.zip` | Gói tất cả để tải về |

> ⚠️ **Chỉ dùng với video bạn sở hữu hoặc được phép sử dụng.**

**So với notebook cũ:** có mốc từng từ; lọc câu Whisper "bịa" khi gặp nhạc nền; bỏ câu lặp vòng; gộp câu quá ngắn và
tách câu quá dài đúng chỗ ngắt; **quét lại khoảng trống bằng VAD tắt để vớt lời bị bỏ sót**; báo cáo QC; tự hạ cấp
GPU → CPU khi gặp lỗi CUDA; có thể nghe lại từng câu ngay trong notebook.
"""),
        md("""
## 0. Chuẩn bị trên Kaggle (làm một lần)

1. **Settings → Accelerator**: chọn GPU (T4 hoặc P100). Không có GPU vẫn chạy được nhưng rất chậm.
2. **Settings → Internet: On** (cần để tải model Whisper lần đầu; có thể cần xác minh số điện thoại).
3. **Add Input → Upload**: tạo dataset chứa video của bạn. Notebook sẽ tự tìm trong `/kaggle/input`.
4. Chạy lần lượt từng ô (**Run All** cũng được).
"""),
        code("%pip install -q \"faster-whisper>=1.1\""),
        writefile_cell(),
        code('''
import os, subprocess

import dubkaggle as dk

# Thư mục làm việc: /kaggle/working trên Kaggle, ./working khi chạy trên máy mình
WORK_ROOT = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.path.abspath("./working")
os.makedirs(WORK_ROOT, exist_ok=True)

print("dubkaggle", dk.__version__)
for cmd in (["nvidia-smi", "-L"], ["ffmpeg", "-version"]):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()
        print(out[0] if out else f"{cmd[0]}: (không có đầu ra)")
    except FileNotFoundError:
        print(f"⚠️ Không thấy lệnh {cmd[0]}")

videos = dk.find_videos()
print("\\nVideo tìm thấy:")
for i, v in enumerate(videos):
    print(f"  [{i}] {v}")
if not videos:
    print("  (chưa có) -> Add Input để thêm dataset chứa video, hoặc điền đường dẫn thủ công ở ô cấu hình")
'''),
        md("""
## 1. Cấu hình

Chỉ cần sửa ô dưới đây. Các tham số quan trọng:

- **`language`**: mã ngôn ngữ của video (`"zh"`, `"ja"`, `"en"`...) hoặc `"auto"`. Chỉ rõ ngôn ngữ thường chính xác hơn `auto`.
- **`model_size`**: `"large-v3"` chính xác nhất; `"medium"` nhanh hơn, ít tốn VRAM hơn.
- **`hotwords`**: **tên riêng cần nhận đúng** (nhân vật, địa danh), cách nhau dấu phẩy. Rất hữu ích cho anime/phim.
- **`separate_vocals`**: tách giọng khỏi nhạc nền bằng Demucs trước khi nhận diện. Nên bật nếu nhạc nền to
  (cần `pip install demucs`, nặng và **chưa được kiểm chứng trên Kaggle thật**, thất bại thì tự dùng audio gốc).
- **`recheck_gaps`**: quét lại các khoảng trống dài bằng VAD tắt để vớt lời bị bỏ sót.
- **`max_seg_seconds`**: câu dài hơn mức này sẽ được tách. Câu ngắn dễ khớp thời gian khi lồng tiếng hơn.
"""),
        code('''
VIDEO = videos[0] if videos else ""        # <- hoặc điền thẳng đường dẫn, ví dụ "/kaggle/input/my-video/ep01.mp4"

cfg = dk.Config(
    video_path=VIDEO,
    work_dir=f"{WORK_ROOT}/work",
    out_dir=f"{WORK_ROOT}/output",
    language="zh",                  # "auto" để tự nhận diện
    model_size="large-v3",
    hotwords="",                    # ví dụ: "哆啦A梦, 大雄, 静香, 胖虎, 小夫"
    separate_vocals=False,          # True nếu nhạc nền quá to
    recheck_gaps=True,
    max_seg_seconds=8.0,
)
print(f"Video: {cfg.video_path or '(CHƯA CHỌN)'}")
'''),
        md("""
## 2. Chạy

Các bước (log in ra theo thứ tự): tách âm thanh 16 kHz và chuẩn hóa âm lượng → (tùy chọn) tách giọng khỏi nhạc →
nhận diện có mốc từng từ → lọc câu bịa / câu lặp → gộp & tách câu → **quét lại khoảng trống** → gắn cờ QC → xuất file.
"""),
        code('''
result = dk.run_transcribe(cfg)
'''),
        md("""
## 3. Kiểm tra chất lượng

Đọc báo cáo QC trước khi dịch. Chú ý:
- **`low_conf` / `no_speech`**: nên nghe lại (ô bên dưới có hàm `listen`).
- **`fast_speech`**: người nói rất nhanh, khi dịch sang tiếng khác dễ bị quá ngắn khung (Bước 2 xử lý).
- **Khoảng trống dài**: có thể bị bỏ sót lời, hoặc chỉ là đoạn nhạc/không thoại.
"""),
        code('''
print(open(result["paths"]["qc_md"], encoding="utf-8").read())
'''),
        code('''
# Xem 25 câu đầu của kịch bản
for line in open(result["paths"]["script"], encoding="utf-8").read().splitlines()[:25]:
    print(line)
'''),
        code('''
# Nghe lại một câu để so với chữ. Ví dụ: listen(12). Dùng id ở cột "#" trong báo cáo QC.
try:
    from IPython.display import Audio, display
except ImportError:           # chạy ngoài Jupyter
    Audio = display = None

AUDIO = f"{cfg.work_dir}/audio_16k.wav"

def listen(seg_id, pad=0.3):
    seg = next(s for s in result["segments"] if s.id == seg_id)
    clip = dk.cut_audio(AUDIO, f"{cfg.work_dir}/listen_{seg_id}.wav", max(0.0, seg.start - pad), seg.end + pad)
    print(f"#{seg.id} [{dk.fmt_bracket(seg.start)} -> {dk.fmt_bracket(seg.end)}] {seg.text}")
    print(f"   cờ: {seg.flags or '-'}")
    if display:
        display(Audio(str(clip)))

# Tự nghe thử câu đầu tiên bị gắn cờ
flagged = [s for s in result["segments"] if set(s.flags) - {"recovered"}]
if flagged:
    listen(flagged[0].id)
else:
    print("Không có câu nào bị gắn cờ.")
'''),
        md("""
## 4. Tải kết quả về

- Trên Kaggle: mở tab **Output** (hoặc **Data → Output** bên phải) và tải `dubstudio_output.zip`.
- **Dùng với DubStudio:**
  - `script.txt` đang là **ngôn ngữ gốc**. Dịch trước rồi mới lồng tiếng: hoặc dùng **Notebook 02** (dịch Gemini + kiểm tra
    tốc độ đọc), hoặc dùng tab **Dịch kịch bản** của DubStudio.
  - Muốn lưu để chạy Bước 2 trong phiên khác: tải `transcript.json` lên thành dataset.
"""),
        code('''
print("Gói kết quả:", result["paths"]["bundle"])
print(f"Số câu: {len(result['segments'])} | phủ {result['qc']['coverage']:.0%} video")
'''),
    ])


# ----------------------------------------------------------------------------- notebook 02
def nb02() -> dict:
    return notebook([
        md("""
# 🌐 DubStudio · Bước 2: Dịch + kiểm tra tốc độ đọc

Nhận `transcript.json` từ Bước 1, rồi:

1. **Dịch bằng Gemini** theo ngữ cảnh: có chủ đề, phong cách, **thuật ngữ**, **quy tắc xưng hô**, mỗi câu có giới hạn ký tự
   theo khung thời gian.
2. **Ước lượng tốc độ đọc** của từng câu dịch để biết câu nào giọng đọc sẽ **không kịp** (nguyên nhân giọng bị "bỏ qua").
3. **Tự rút gọn** các câu quá nhanh bằng Gemini (chỉ nhận bản ngắn hơn và không rỗng).
4. Xuất `script_<ngôn ngữ>.txt` để nạp vào DubStudio, kèm `fit_report.md`.

> ⚠️ Chỉ dùng với nội dung bạn có quyền sử dụng. Cần **Internet: On** và key Gemini trong **Add-ons → Secrets**
> (tên `GEMINI_API_KEY`). Không dán key trực tiếp vào notebook.
"""),
        code("%pip install -q google-genai"),
        writefile_cell(),
        code('''
import glob, os

import dubkaggle as dk

WORK_ROOT = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.path.abspath("./working")
os.makedirs(WORK_ROOT, exist_ok=True)

api_key = dk.get_secret("GEMINI_API_KEY")
print("Key Gemini:", "✓ đã có" if api_key else "✗ CHƯA CÓ (thêm ở Add-ons → Secrets, tên GEMINI_API_KEY)")

found = (glob.glob("/kaggle/input/**/transcript.json", recursive=True)
         + glob.glob(f"{WORK_ROOT}/output/transcript.json"))
print("transcript.json tìm thấy:")
for i, p in enumerate(found):
    print(f"  [{i}] {p}")
'''),
        md("""
## 1. Xem model Gemini đang dùng được

Tên model đổi thường xuyên, nên **hãy liệt kê từ API thay vì đoán**. Model nào không dùng được với key của bạn
(lỗi 403/404) sẽ tự bị bỏ qua khi dịch.
"""),
        code('''
if api_key:
    try:
        models = dk.list_gemini_models(api_key)
        print(f"{len(models)} model khả dụng:")
        for m in models:
            print("  ", m)
    except Exception as e:          # noqa: BLE001
        print("Không liệt kê được model:", e)
'''),
        md("""
## 2. Cấu hình dịch

- **`glossary`**: thuật ngữ/tên riêng, dạng `Tên gốc=Tên dịch`, cách nhau dấu phẩy.
- **`pronoun_rules`**: quy tắc xưng hô. Viết rõ **ai gọi ai thế nào**, model sẽ giữ nhất quán suốt kịch bản.
- **`speed_cap`**: tốc độ đọc tối đa bạn chấp nhận (1.25 = nhanh hơn 25%). Câu cần nhanh hơn mức này sẽ được rút gọn.
- **`cps`**: số ký tự giọng đọc đọc được mỗi giây. Để `0` dùng giá trị ước lượng; **nên đo thật** ở ô bên dưới.
"""),
        code('''
TRANSCRIPT = found[0] if found else ""      # <- hoặc điền đường dẫn thủ công

tcfg = dk.TranslateConfig(
    source_lang="zh",
    target_lang="vi",
    model="gemini-3.5-flash",       # sửa theo danh sách model ở trên nếu cần
    topic="",                       # ví dụ: "phim hoạt hình thiếu nhi, giọng vui vẻ"
    style="Tự nhiên, dễ nghe, đúng văn nói",
    glossary="",                    # ví dụ: "大雄=Nobita, 哆啦A梦=Doraemon"
    pronoun_rules="",               # ví dụ: "Nobita gọi Doraemon là 'Doraemon'; Doraemon xưng 'tớ', gọi Nobita là 'cậu'"
    speed_cap=1.25,
    cps=0,                          # 0 = ước lượng mặc định; điền số đo được ở ô hiệu chỉnh bên dưới
    shorten_rounds=2,
    out_dir=f"{WORK_ROOT}/output_translate",
)
print("Transcript:", TRANSCRIPT or "(CHƯA CHỌN)")
'''),
        md("""
## 3. (Tùy chọn) Đo tốc độ đọc thật của giọng Edge-TTS

Ước lượng mặc định chỉ là con số bảo thủ. Đặt `CALIBRATE = True` để đọc thử vài câu mẫu bằng Edge-TTS
rồi đo ký tự/giây thật. Điền kết quả vào `cps` ở ô cấu hình trên. *(Cần Internet; dịch vụ Edge-TTS không chính thức,
chỉ nên dùng để thử nghiệm.)*
"""),
        code('''
CALIBRATE = False
VOICE = "vi-VN-HoaiMyNeural"
CALIB_SAMPLES = [
    "Hôm nay mình sẽ kể cho các bạn nghe một câu chuyện rất thú vị.",
    "Nhưng khi chui xuống gầm chiếu, cậu ấy phát hiện ra một điều lạ.",
    "Đúng lúc này, cả nhóm quyết định quay về căn cứ để kiểm tra.",
    "Thật may là chiếc phi thuyền vẫn còn nguyên vẹn.",
    "Họ phải nhanh chóng rời khỏi nơi này trước khi trời tối.",
]

if CALIBRATE:
    import asyncio, subprocess, sys, tempfile, time
    from concurrent.futures import ThreadPoolExecutor
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "edge-tts"], check=True)
    import edge_tts

    def synth_seconds(text):
        path = tempfile.mktemp(suffix=".mp3")
        # Jupyter đã có sẵn event loop, nên chạy asyncio trong luồng riêng
        with ThreadPoolExecutor(1) as ex:
            ex.submit(asyncio.run, edge_tts.Communicate(text, VOICE).save(path)).result()
        time.sleep(0.5)                       # nghỉ giữa các lần gọi để tránh bị giới hạn tốc độ
        return dk.probe_duration(path)

    measured = dk.calibrate_cps(CALIB_SAMPLES, synth_seconds)
    print(f"Tốc độ đọc đo được: {measured} ký tự/giây (đã tính cả khoảng trắng)")
    print("-> Điền cps=" + str(measured) + " vào ô cấu hình rồi chạy lại từ ô cấu hình.")
else:
    print("Bỏ qua hiệu chỉnh (CALIBRATE=False). Dùng ước lượng mặc định:", dk.target_cps(tcfg), "ký tự/giây")
'''),
        md("""
## 4. Chạy dịch + rút gọn

Log cho thấy số câu quá nhanh **trước** và **sau** khi rút gọn. Nếu sau rút gọn vẫn còn nhiều câu quá nhanh, xem
`fit_report.md` để quyết định: tăng `speed_cap`, sửa tay vài câu, hoặc làm chậm video (xem gợi ý trong báo cáo).
"""),
        code('''
generate_fn = dk.make_gemini_generate(api_key)
res = dk.run_translate(TRANSCRIPT, tcfg, generate_fn)
'''),
        code('''
print(open(res["paths"]["fit_md"], encoding="utf-8").read())
'''),
        code('''
# So sánh nhanh bản gốc và bản dịch (15 câu đầu)
for s in res["segments"][:15]:
    print(f"[{dk.fmt_bracket(s.start)}] {s.src}")
    print(f"{'':16}-> {s.text}")
'''),
        md("""
## 5. Dùng trong DubStudio

1. Tải `dubstudio_translate_<ngôn ngữ>.zip` từ tab Output.
2. Trong DubStudio, tab **Dub Studio** bấm **📂 Mở...** và chọn `script_<ngôn ngữ>.txt`.
3. Đọc lại, sửa tay nếu cần, rồi bấm **② Lồng tiếng & xuất video**.

`fit_report.md` cho biết còn câu nào có nguy cơ bị đọc không kịp; xử lý những câu đó trước khi lồng tiếng.
"""),
        code('''
print("Gói kết quả:", res["paths"]["bundle"])
print(f"Còn {res['after']['too_fast']}/{res['after']['lines']} câu quá nhanh "
      f"(trước rút gọn: {res['before']['too_fast']}).")
'''),
    ])


def build(out_dir: Path) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, nb in (("01_transcribe.ipynb", nb01()), ("02_translate_fit.ipynb", nb02())):
        p = out_dir / name
        p.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.append(p)
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "notebooks"
    for path in build(target):
        print("đã tạo", path)
