# 🎬 DubStudio

[![CI](https://github.com/tai194206/DubStudio/actions/workflows/ci.yml/badge.svg)](https://github.com/tai194206/DubStudio/actions/workflows/ci.yml)
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

### ✅ Đã hoàn thiện & đã kiểm chứng tự động (Offline + CI)
- **Kiến trúc tách lớp:** `core/` không import Tkinter. Log, tiến độ lồng nhau (`scope()`) và hủy tác vụ đi qua `Reporter`.
- **Cấu hình:** đọc API key từ `.env`, lưu cấu hình cục bộ vào `settings.json` (`core/config.py`, `core/languages.py`).
- **FFmpeg (`core/ffmpeg_utils.py`):** tự dò FFmpeg/FFprobe, đo thời lượng và số luồng media, tăng/giảm tốc giọng giữ nguyên cao độ bằng `atempo`.
- **Kịch bản đa định dạng (`core/subtitles.py`):** SRT, `[start -> end] text`, và CSV cũ. Đã kiểm chứng trên dữ liệu thật: 159 câu (SRT) và 160 câu (dạng bracket).
- **Mô hình dữ liệu chuẩn (`core/models.py`):** `Segment` và `WordTiming(word, start, end)`, helper `scale_word_timings` tự động quy đổi mốc từ khi audio co giãn tốc độ và ghép vào timeline.
- **Cache audio thô & metadata (`core/cache.py`):** Băm khóa SHA-256 từ `(schema_version, engine, voice_id, engine_params, text)`, không chứa `speed_rate` để tối ưu tái sử dụng. Ghi nguyên tử (.tmp -> replace), tự phát hiện và phục hồi khi file rỗng/hỏng.
- **Engine Phụ đề động Karaoke ASS (`core/subtitle_engine.py`):** Dùng `pysubs2` sinh file `.ass` với tag `\kf`/`\k` đổi màu highlight từng từ, viền đen và bóng đổ rõ nét. Burn phụ đề vào video bằng FFmpeg qua đường dẫn tương đối an toàn trên Windows.
- **Dịch theo ngữ cảnh (`core/translator.py`):** Gemini trả JSON theo `id`, tự chuyển model dự phòng khi model không khả dụng, tự dịch lại các câu bị thiếu. Đã kiểm chứng bằng model giả.
- **Khớp thời gian & ghép track (`core/timefit.py`, `core/audio_track.py`):** tính hệ số tăng tốc cho từng câu, cho phép câu mượn khoảng lặng phía sau, ghép track theo mốc thời gian và căn chỉnh mốc từ thực tế.
- **Trộn âm (`core/mixer.py`):** Force Mix stereo 44.1kHz, ducking bằng `sidechaincompress`, hỗ trợ burn trực tiếp phụ đề `.ass` karaoke hoặc `.srt`.
- **TTS adapter (`core/tts.py`):** `ToneTTS` (sinh âm thanh và WordTiming giả lập offline), `FallbackTTS` (ElevenLabs lỗi thì chuyển Edge-TTS).
- **Pipeline (`core/pipeline.py`):** tích hợp liền mạch cache, đồng bộ WordTiming và xuất video kèm phụ đề karaoke.
- **CLI (`cli.py`):** hỗ trợ `render` và `clear-cache` (giữ lại `cache/.gitkeep`), ép terminal xuất UTF-8.
- **GUI (`gui/` & `run.py`):** kiểm chứng import, khởi tạo giao diện `DubStudio`, nút `Mở...` thoáng đẹp.
- **Test tự động:** 26 test (`pytest`) chạy hoàn toàn offline không cần internet hay API key.

### 🧪 Đã viết mã - Cần kiểm thử thủ công (chưa tự động hóa trong CI)
- **ElevenLabs with-timestamps:** Gọi endpoint `/v1/text-to-speech/{voice_id}/with-timestamps`, gom ký tự thành từng từ (cần API key và quota thật).
- **Edge-TTS WordBoundary:** Stream websocket và nhận sự kiện `WordBoundary` thật từ Microsoft (cần kết nối internet).
- **Karaoke trên giọng nói thật:** Kiểm tra độ khớp khẩu hình và chữ nhảy màu trên tai và mắt người dùng.
- **Fallback `faster-whisper`:** Căn mốc từ tự động khi engine không hỗ trợ timestamp (cần model Whisper thật).
- `core/downloader.py` (cần link YouTube thật), `core/separator.py` (Demucs, cần PyTorch).

### 📝 Kế hoạch (Phase 3)
- Video dọc 9:16 Shorts/Recap với hiệu ứng làm mờ nền (blur background).
- `config/prompts.py`, `assets/` kèm file giấy phép.

---

## 🏗 Cấu trúc dự án

```text
tdtu_auto_dubber/
├── core/                       # Lõi xử lý, không phụ thuộc GUI
│   ├── config.py               # [x] .env, settings.json, default models
│   ├── cache.py                # [x] Cache audio thô & metadata WordTiming (SHA-256)
│   ├── models.py               # [x] Segment, WordTiming, scale_word_timings
│   ├── languages.py            # [x] Ngôn ngữ & giọng đọc
│   ├── errors.py               # [x] Ngoại lệ có thông điệp rõ ràng
│   ├── reporter.py             # [x] Log, tiến độ lồng nhau, hủy tác vụ
│   ├── ffmpeg_utils.py         # [x] FFmpeg/FFprobe, atempo
│   ├── subtitles.py            # [x] Parser SRT / [a -> b] / CSV cũ
│   ├── subtitle_engine.py      # [x] Phụ đề Karaoke .ass, burn vào video
│   ├── translator.py           # [x] Gemini (JSON theo id, đổi model dự phòng)
│   ├── tts.py                  # [x] ToneTTS, FallbackTTS, Edge-TTS, ElevenLabs
│   ├── timefit.py              # [x] Tính hệ số tăng tốc từng câu
│   ├── audio_track.py          # [x] Ghép track theo mốc thời gian, căn WordTiming
│   ├── mixer.py                # [x] Force Mix 44.1kHz, ducking, burn ASS/SRT
│   ├── pipeline.py             # [x] Điều phối luồng dubbing tích hợp cache & phụ đề
│   ├── diagnostics.py          # [ ] Kiểm tra môi trường (chưa thử)
│   ├── downloader.py           # [ ] Tải video bằng yt-dlp (chưa thử)
│   ├── transcriber.py          # [ ] faster-whisper (chưa thử)
│   └── separator.py            # [ ] Demucs, tùy chọn (chưa thử)
├── gui/                        # [x] Tkinter, đã kiểm chứng import và chạy thử
├── tests/                      # [x] pytest (26 test offline 100%), conftest tạo video mẫu
├── config/prompts.py           # [ ] Kế hoạch
├── assets/                     # [ ] Kế hoạch: font & nhạc nền kèm license
├── .github/workflows/ci.yml    # [x] Đã cấu hình chạy test trên Ubuntu
├── cache/                      # [x] Thư mục cache băm SHA-256 (bảo toàn .gitkeep)
├── temp_processing/            # Đã ignore, file tạm khi render
├── output/                     # Đã ignore, video thành phẩm
├── run.py                      # [x] Điểm vào GUI DubStudio (đã kiểm chứng)
├── cli.py                      # [x] Điểm vào CLI (render, clear-cache)
├── requirements.txt            # Thư viện bắt buộc (kèm pysubs2)
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

### Phase 2: Cache & phụ đề động Karaoke
- [x] Thư mục `cache/` riêng (tách khỏi `temp_processing/`), khóa băm SHA-256 từ `(schema_version, engine, voice_id, engine_params, text)`, không chứa `speed_rate`.
- [x] Lệnh `clear-cache` trong CLI xóa cache nhưng bảo toàn `cache/.gitkeep`.
- [x] `core/models.py` với `Segment` mở rộng và `WordTiming(word, start, end)` kèm helper `scale_word_timings`.
- [x] `ToneTTS` tự sinh `WordTiming` chia đều để kiểm thử tự động offline.
- [x] `core/subtitle_engine.py` sinh file `.ass` karaoke chuẩn UTF-8 với tag `\kf`/`\k` và burn vào video an toàn trên Windows.
- [x] Bộ test tự động offline 100% (26 test passed) bao gồm `test_cache.py` và `test_subtitle_engine.py`.
- [ ] Kiểm thử thủ công: `WordBoundary` của Edge-TTS thật qua WebSocket, `/with-timestamps` của ElevenLabs thật, và fallback `faster-whisper`.

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
