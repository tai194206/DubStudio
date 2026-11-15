# 🎬 DubStudio

![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-orange.svg)
![Status](https://img.shields.io/badge/Status-In%20Development-yellow.svg)

**DubStudio** là công cụ tự động hóa lồng tiếng (Auto Dubbing) cho video, viết bằng Python. Dự án hướng tới mở rộng sang dựng video dọc dạng ngắn (Shorts/Recap 9:16).

Kiến trúc tách lớp giữa **lõi xử lý (`core/`)** và **giao diện (`gui/`)**: lõi không phụ thuộc Tkinter, nên cùng một pipeline có thể dùng cho GUI, CLI (kế hoạch) hoặc giao diện khác sau này.

---

## ⚠️ Phạm vi sử dụng & Miễn trừ trách nhiệm

- **Chỉ dùng cho nội dung hợp lệ:** video bạn sở hữu bản quyền, tư liệu được cấp phép (stock footage có giấy phép, public domain, tài sản AI tự tạo, hoặc thỏa thuận lồng tiếng với chủ sở hữu).
- **Không có chức năng né bản quyền:** dự án không có và sẽ không phát triển tính năng cắt vụn, xáo trộn cảnh, đổi màu hay biến đổi âm thanh nhằm qua mặt Content ID. "Fair Use" không phải lá chắn pháp lý mặc định và không áp dụng giống nhau ở mọi quốc gia.
- **Rủi ro kỹ thuật & thương mại:**
  - **Edge-TTS** dùng dịch vụ đọc của Microsoft qua giao thức không chính thức. Phù hợp để phát triển và thử nghiệm, không nên dùng trực tiếp cho sản phẩm thương mại. Khi xuất bản hoặc bán dịch vụ, dùng **ElevenLabs** hoặc engine có giấy phép thương mại.
  - **ElevenLabs:** kiểm tra gói cước phù hợp với điều khoản thương mại và quy định nhân bản giọng nói.
  - **Tài nguyên đi kèm (font, nhạc nền):** phải có nguồn gốc và giấy phép rõ ràng, lưu kèm file license trong `assets/`.
  - **Chính sách nền tảng:** YouTube và TikTok siết nội dung sản xuất hàng loạt thiếu giá trị gốc. Công cụ chỉ tự động hóa khâu kỹ thuật, không thay thế góc nhìn biên tập của người làm nội dung.

---

## ✨ Trạng thái tính năng

### ✅ Đã hoàn thiện & đã kiểm chứng
- **Kiến trúc tách lớp:** `core/` không import Tkinter. Log, tiến độ lồng nhau (`scope()`) và hủy tác vụ đi qua `Reporter`.
- **Cấu hình:** đọc API key từ `.env`, lưu cấu hình cục bộ vào `settings.json` (`core/config.py`, `core/languages.py`).
- **FFmpeg (`core/ffmpeg_utils.py`):** tự dò FFmpeg/FFprobe, đo thời lượng và số luồng media, tăng/giảm tốc giọng giữ nguyên cao độ bằng `atempo`.
- **Kịch bản đa định dạng (`core/subtitles.py`):** SRT, `[start -> end] text`, và CSV cũ. Đã kiểm chứng trên dữ liệu thật: 159 câu (SRT) và 160 câu (dạng bracket).
- **Dịch theo ngữ cảnh (`core/translator.py`):** Gemini trả JSON theo `id`, tự chuyển model dự phòng khi model không khả dụng, tự dịch lại các câu bị thiếu. Đã kiểm chứng bằng model giả.
- **Khớp thời gian & ghép track (`core/timefit.py`, `core/audio_track.py`):** tính hệ số tăng tốc cho từng câu, cho phép câu mượn khoảng lặng phía sau, ghép track theo mốc thời gian.
- **Trộn âm (`core/mixer.py`):** Force Mix stereo 44.1kHz, ducking bằng `sidechaincompress`, tự xử lý video có tiếng và không có tiếng.
- **TTS adapter (`core/tts.py`):** `ToneTTS` (engine giả, không cần API), `FallbackTTS` (ElevenLabs lỗi thì chuyển Edge-TTS).
- **Pipeline (`core/pipeline.py`):** đã chạy end-to-end trên video mẫu 12 giây, cả có và không có tiếng gốc.
- **CLI (`cli.py`):** hỗ trợ `render` và `clear-cache`, đã kiểm chứng chạy end-to-end với `ToneTTS` trên video mẫu tạo bằng FFmpeg.
- **GUI (`gui/` & `run.py`):** đã kiểm chứng import, khởi tạo và tương tác giữa toàn bộ các tab (`studio`, `downloader`, `translator`, `settings`) với `core/`.
- **Test tự động:** 19 test (`pytest`) chạy hoàn toàn offline không cần internet hay API key.

### 🧪 Đã viết nhưng chưa kiểm chứng thực tế
- `core/downloader.py`, `core/transcriber.py`: chưa thử với mạng hoặc model thật.
- `core/separator.py` (Demucs, tùy chọn vì cần PyTorch vài GB): chưa thử.
- `core/diagnostics.py`: chưa thử.
- Gọi API thật của Gemini và ElevenLabs: mới kiểm thử bằng hàm giả.
- `.github/workflows/ci.yml`: đã cấu hình, chưa chạy trên GitHub. Chỉ thêm badge CI sau khi workflow xanh.

### 📝 Kế hoạch (file đang là placeholder)
- `core/models.py`, `core/subtitle_engine.py`, `config/prompts.py`, `assets/`.
- Thư mục `cache/` theo hash `(text, voice, engine, speed)`.

---

## 🏗 Cấu trúc dự án

```text
tdtu_auto_dubber/
├── core/                       # Lõi xử lý, không phụ thuộc GUI
│   ├── config.py               # [x] .env, settings.json
│   ├── languages.py            # [x] Ngôn ngữ & giọng đọc
│   ├── errors.py               # [x] Ngoại lệ có thông điệp rõ ràng
│   ├── reporter.py             # [x] Log, tiến độ lồng nhau, hủy tác vụ
│   ├── ffmpeg_utils.py         # [x] FFmpeg/FFprobe, atempo
│   ├── subtitles.py            # [x] Parser SRT / [a -> b] / CSV cũ
│   ├── translator.py           # [x] Gemini (JSON theo id, đổi model dự phòng)
│   ├── tts.py                  # [x] ToneTTS, FallbackTTS, Edge-TTS, ElevenLabs
│   ├── timefit.py              # [x] Tính hệ số tăng tốc từng câu
│   ├── audio_track.py          # [x] Ghép track theo mốc thời gian
│   ├── mixer.py                # [x] Force Mix 44.1kHz, ducking
│   ├── pipeline.py             # [x] Điều phối luồng dubbing
│   ├── diagnostics.py          # [ ] Kiểm tra môi trường (chưa thử)
│   ├── downloader.py           # [ ] Tải video bằng yt-dlp (chưa thử)
│   ├── transcriber.py          # [ ] faster-whisper (chưa thử)
│   ├── separator.py            # [ ] Demucs, tùy chọn (chưa thử)
│   ├── models.py               # [ ] Kế hoạch: WordTiming
│   └── subtitle_engine.py      # [ ] Kế hoạch: phụ đề .ass karaoke
├── gui/                        # [x] Tkinter, đã kiểm chứng import và chạy thử
├── tests/                      # [x] pytest (19 test), conftest tạo video mẫu bằng FFmpeg
├── config/prompts.py           # [ ] Kế hoạch
├── assets/                     # [ ] Kế hoạch: font & nhạc nền kèm license
├── .github/workflows/ci.yml    # [ ] Đã cấu hình, chưa chạy trên GitHub
├── cache/                      # [ ] Kế hoạch, đã ignore
├── temp_processing/            # Đã ignore, file tạm khi render
├── output/                     # Đã ignore, video thành phẩm
├── run.py                      # [x] Điểm vào GUI (đã kiểm chứng)
├── cli.py                      # [x] Điểm vào CLI (render, clear-cache)
├── requirements.txt            # Thư viện bắt buộc
├── requirements-optional.txt   # faster-whisper, demucs
├── requirements-dev.txt        # pytest, imageio-ffmpeg cho test
├── .env.example
└── .gitignore
```

---

## 🛠 Cài đặt & chạy

### Yêu cầu
- Python 3.10 trở lên
- FFmpeg và FFprobe trong `PATH` (hoặc `C:\ffmpeg\bin\`, hoặc `pip install imageio-ffmpeg`)

### Cài đặt
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # rồi điền GEMINI_API_KEY và ELEVENLABS_API_KEY
```

Tùy chọn (nặng hơn): `pip install -r requirements-optional.txt`

### Chạy test
```bash
pip install -r requirements-dev.txt
pytest -q
```

### Mở giao diện (chưa kiểm chứng thực tế)
```bash
python run.py
```

---

## 🗺 Lộ trình

### Phase 1: Lõi dubbing, CLI & kiểm thử tự động
- [x] Tách `core/` khỏi GUI; `reporter`, `errors`, `ffmpeg_utils`.
- [x] Parser phụ đề, dịch Gemini, khớp giờ, ghép track, trộn âm, `ToneTTS`, `FallbackTTS`.
- [x] Pipeline end-to-end trên video mẫu 12 giây (có và không có tiếng).
- [x] `tests/` với 19 test và `tests/conftest.py` tạo video mẫu bằng FFmpeg.
- [x] Xây dựng `cli.py` dùng chung `core/` (hỗ trợ `render` và `clear-cache`).
- [x] Kiểm chứng import và khởi tạo giao diện `gui/` (`run.py`).
- [ ] Kiểm thử thực tế `downloader.py`, `transcriber.py`, `diagnostics.py`, và gọi API thật.
- [ ] Chạy GitHub Actions (`.github/workflows/ci.yml`) xanh, rồi mới thêm badge.

**Tiêu chí nghiệm thu:** render video 10 phút với 100 câu, lệch thời gian dưới 0.1s mỗi câu; một câu lỗi API không làm sập pipeline; toàn bộ test pass trên GitHub Actions mà không gọi API thật.

### Phase 2: Cache & phụ đề động
- [ ] Thư mục `cache/` riêng (tách khỏi `temp_processing/`), khóa theo hash `(text, voice, engine, speed)`, cờ `--clear-cache`.
- [ ] `core/models.py` với `Segment` và `WordTiming(word, start, end)`.
- [ ] Mốc từng từ theo thứ tự: `WordBoundary` của Edge-TTS → `alignment` của ElevenLabs → `faster-whisper` (chỉ khi engine không trả mốc).
- [ ] `core/subtitle_engine.py` sinh `.ass` karaoke và burn vào video.

**Tiêu chí nghiệm thu:** sửa 1 câu trong kịch bản 50 câu chỉ tạo lại đúng 1 file audio; phụ đề khớp từng từ với cả Edge-TTS và ElevenLabs.

### Phase 3: Video dọc 9:16 (Shorts / Storytelling)
- [ ] Khung dọc 1080x1920: nền mờ phía sau, khung chính ở giữa, tiêu đề cố định phía trên.
- [ ] Ghép tư liệu hợp lệ (clip tự quay, stock có giấy phép, ảnh AI với hiệu ứng Ken Burns) theo mạch lời kể.
- [ ] Giọng nhanh 1.15–1.25x, nghỉ 0.15–0.2s giữa các câu; nhạc nền hợp lệ ở mức 10–15% có ducking.
- [ ] `config/prompts.py` và `assets/` kèm đầy đủ file license.

**Tiêu chí nghiệm thu:** xuất video dọc 1080x1920 từ kịch bản và thư mục tư liệu hợp lệ bằng một lệnh; nhạc nền không đè giọng; phụ đề nằm trong vùng an toàn của màn hình dọc.

---

## 📄 Giấy phép

Chưa chọn giấy phép. Trước khi đưa lên GitHub công khai, cần quyết định giấy phép cho mã nguồn và kiểm tra giấy phép của từng thư viện phụ thuộc.
