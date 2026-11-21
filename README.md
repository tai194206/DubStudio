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

### 🚀 Nâng cấp mới (Story Studio 9:16 & Pipeline V5.2 — 229 tests PASS)
- **Whisper Alignment Cache (`core/story_pipeline.py`):** Khi dùng `full_audio.mp3/.wav`, tự căn mốc từng từ bằng `faster-whisper` ở `1.00x` và lưu cache `cache/align_*.json` (`0.01s`), tái sử dụng khi đổi tốc độ hoặc render lại.
- **Phụ đề CapCut 3 chữ (`highlight_3words`):** Hiển thị tối đa 3 chữ theo cụm và làm sáng chữ đang đọc, ngắt ranh giới từng cảnh sạch sẽ không tràn chữ sang cảnh sau.
- **Đồng bộ tốc độ đầu ra & BGM `.mp4` (`core/story_pipeline.py`, `gui/story_tab.py`):** Điều chỉnh tốc độ đồng bộ **Video + Giọng + Phụ đề**, hỗ trợ rút nhạc nền từ file `.mp4/.mov/.mkv/.webm` và giữ nguyên nhịp gốc `1.00x` cắt khớp thời lượng video.
- **Bộ lọc Né quét AI (`core/anti_ai_filter.py`):** Hỗ trợ 3 mức **Nhẹ / Vừa / Mạnh** không hở góc đen, xóa metadata/C2PA, tích hợp **1-pass** trước lớp phụ đề `.ass` trên Tab 3 và có tab riêng **`4. Né quét AI`** cho file `.mp4` hoàn chỉnh.
- **SFX Engine (`core/sfx_engine.py`):** Hỗ trợ **20 tag SFX điện ảnh V5.2** (kèm `none`), tự tổng hợp fallback sạch bằng NumPy/FFT khi thiếu file WAV, trộn `normalize=0` + EQ tách dải giọng đọc + `alimiter` chống rè và thanh trượt **Âm lượng SFX** (`0%–150%`) trên GUI.
- **Pipeline V5.2 & Flow Bot Batch (`automation/`, `.agent/`):** Phân hạng ưu tiên tư liệu thật `0_100` → `30_70` → `50_50` → `70_30`, Hook 3 giây đầu, trình kiểm định `validate_storyboard.py` và `flow_bot.py` batch pipelining (xem [docs/V5_QUICKSTART.md](docs/V5_QUICKSTART.md)).

### ✅ Đã hoàn thiện & đã kiểm chứng tự động (Offline + CI — 229 tests PASS)
- **Kiến trúc tách lớp:** `core/` không import Tkinter. Log, tiến độ lồng nhau (`scope()`) và hủy tác vụ đi qua `Reporter`.
- **Cấu hình & Môi trường:** Đọc API key từ `.env`, lưu cấu hình vào `settings.json` (`core/config.py`, `core/languages.py`).
- **FFmpeg (`core/ffmpeg_utils.py`):** Tự dò binary, probe metadata thời lượng/luồng, điều chỉnh tốc độ giọng `atempo` giữ nguyên cao độ.
- **Kịch bản đa định dạng (`core/subtitles.py`):** Parser SRT, `[start -> end] text`, và CSV cũ.
- **Mô hình dữ liệu chuẩn (`core/models.py`):** `Segment` và `WordTiming(word, start, end)` cùng helper `scale_word_timings` co giãn và dịch chuyển mốc từ khi đổi tốc độ.
- **Cache audio thô & metadata (`core/cache.py`):** Băm khóa SHA-256 từ `(schema_version, engine, voice_id, engine_params, text)`, không chứa `speed_rate` để tái sử dụng tối đa. Ghi nguyên tử (.tmp -> replace), tự phát hiện và tái tạo khi cache hỏng/rỗng.
- **Engine Phụ đề động Karaoke ASS (`core/subtitle_engine.py`):** Dùng `pysubs2` sinh `.ass` chuẩn UTF-8 với tag `\kf`/`\k` highlight từng từ. Burn vào video bằng FFmpeg qua đường dẫn tương đối an toàn trên Windows.
- **Dịch theo ngữ cảnh (`core/translator.py`):** 
  - Gemini trả JSON theo `id`, tự chuyển sang model tiếp theo khi gặp mã lỗi 404 hoặc 403 / permission denied.
  - Báo lỗi `DubberError` khi toàn bộ model trong chuỗi đều không khả dụng.
  - Tự động dịch lại câu thiếu.
  - Inject quy tắc xưng hô `pronoun_rules` vào prompt hệ thống và payload.
  - `resolve_model()` tự động chọn model mới nhất khớp quy tắc `TASK_PROFILES` (Nhanh / Cân bằng / Phức tạp).
- **Trình đọc âm thanh Whisper (`core/transcriber.py`):** `_read_wav_float32(path)` đọc WAV 16-bit mono 16 kHz thành mảng `float32`, lazy import `numpy` và bỏ qua PyAV tránh lỗi DLL trên Windows. Đã có test kiểm tra chặt các điều kiện tần số, số kênh và kích thước mẫu.
- **Khớp thời gian & ghép track (`core/timefit.py`, `core/audio_track.py`):** Co giãn tốc độ theo câu, mượn khoảng lặng, ghép track và đồng bộ mốc WordTiming.
- **Trộn âm (`core/mixer.py`):** Force Mix stereo 44.1kHz, ducking `sidechaincompress`, burn trực tiếp `.ass` hoặc `.srt`.
- **Pipeline (`core/pipeline.py`) & CLI (`cli.py`):** Tích hợp end-to-end, lệnh `render` và `clear-cache`, ép terminal xuất UTF-8.
- **GUI Instantiation (`gui/` & `run.py`):** Khởi tạo đầy đủ các tab `DubStudio` (StudioTab, TranslatorTab, DownloaderTab, SettingsTab) không lỗi import.

### 🧪 Đã viết mã - Cần kiểm thử thủ công (yêu cầu mạng thật & API key thật)
- **Nhận diện giọng thật (Whisper trên Windows):** Chạy nút "① Tạo kịch bản" trên video thật để xác nhận tốc độ và độ chính xác của `faster-whisper`.
- **Chất lượng xưng hô và phong cách dịch:** Kiểm tra Gemini dịch thực tế với API key thật xem có tuân thủ đúng `pronoun_rules` và preset phong cách giữa các nhân vật.
- **Nút "Làm mới" model:** Lấy danh sách model thật từ Gemini API qua kết nối mạng, kiểm tra combobox "Mức độ việc" tự động map đúng model.
- **Edge-TTS `WordBoundary`:** Kiểm tra nghe giọng đọc thật và độ khớp màu chữ karaoke trên video thành phẩm.
- **ElevenLabs with-timestamps:** Gọi endpoint `/v1/text-to-speech/{voice_id}/with-timestamps` với key thật để kiểm tra độ chính xác của timestamp ký tự.

### ⚠️ Chưa xác nhận / Chưa kiểm thử thực tế
- `core/downloader.py` (tải video từ link YouTube bằng yt-dlp).
- `core/separator.py` (tách nhạc nền bằng Demucs, cần cài PyTorch và tải weights).
- `core/diagnostics.py` (kiểm tra môi trường phần cứng).

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
│   ├── transcriber.py          # [x] faster-whisper (đã kiểm chứng trên Windows, bypass PyAV)
│   └── separator.py            # [ ] Demucs, tùy chọn (chưa thử)
├── gui/                        # [x] Tkinter, đã kiểm chứng import và chạy thử
├── tests/                      # [x] pytest (38 test offline 100%), conftest tạo video mẫu
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
