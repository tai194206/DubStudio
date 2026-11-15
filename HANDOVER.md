# 📋 BẢN BÀN GIAO KỸ THUẬT DỰ ÁN DUBSTUDIO
*Tài liệu tóm tắt ngữ cảnh & hướng dẫn dành riêng cho AI Agent / Senior Engineer tiếp quản dự án.*

---

## 1. Tổng quan dự án & Triết lý thiết kế

- **Tên dự án:** `DubStudio` (trước đây là `tdtu_auto_dubber`).
- **Mục tiêu:** Tự động hóa pipeline lồng tiếng cho video:
  $$\text{Video/URL} \longrightarrow \text{Transcribe (Whisper)} \longrightarrow \text{Translate (Gemini)} \longrightarrow \text{TTS (Tone / Edge / ElevenLabs)} \longrightarrow \text{Timefit \& Mix} \longrightarrow \text{Export MP4}$$
- **Môi trường hoạt động:** Windows (Python 3.12, chạy qua `.venv`), có hỗ trợ CI Linux (Ubuntu).
- **Quy tắc bất di bất dịch (Crucial Constraints):**
  1. **Clean Layer Decoupling:** Tuyệt đối **KHÔNG** import `tkinter` hay bất kỳ module nào thuộc `gui/` vào bên trong `core/`. Mọi tương tác tiến độ/log từ lõi phải thông qua class `core.reporter.Reporter`.
  2. **Zero External API Calls in Tests:** 100% test trong `tests/` phải chạy offline độc lập, không gọi API thật (dùng `ToneTTS`, model giả lập, mock hoặc fixture video tạo từ FFmpeg lavfi).
  3. **Không tính năng né bản quyền:** Không phát triển các tính năng lách Content ID (cắt vụn cảnh, đổi pitch lách bản quyền...).

---

## 2. Cấu trúc thư mục & Hiện trạng các module

```text
tdtu_auto_dubber/
├── core/                       # [LÕI ĐỘC LẬP - KHÔNG PHỤ THUỘC GUI]
│   ├── config.py               # Quản lý .env, settings.json, default models, Dataclass Keys & DubSettings
│   ├── cache.py                # Cache audio thô & metadata WordTiming (SHA-256 không chứa speed_rate)
│   ├── models.py               # Segment, WordTiming(word, start, end), helper scale_word_timings
│   ├── languages.py            # Danh mục ngôn ngữ, mã giọng Edge-TTS tương ứng
│   ├── errors.py               # Hệ thống ngoại lệ: DubberError, TTSError, MissingDependency, Cancelled
│   ├── reporter.py             # Kênh log & tiến độ đa tầng (hỗ trợ scope lồng nhau, event hủy tác vụ)
│   ├── ffmpeg_utils.py         # Wrapper FFmpeg: dò binary, probe media info, filter atempo tốc độ giọng
│   ├── subtitles.py            # Parser kịch bản đa định dạng: SRT, bracket [0.5s -> 2.0s], CSV cũ
│   ├── subtitle_engine.py      # Engine sinh phụ đề Karaoke .ass (pysubs2) với tag \kf/\k, burn qua FFmpeg
│   ├── translator.py           # Dịch Gemini (JSON mapping theo ID câu, tự fallback model khi 404, dịch bù)
│   ├── tts.py                  # TTSEngine: ToneTTS (sinh WordTiming), EdgeTTS (WordBoundary), ElevenLabsTTS (with-timestamps), FallbackTTS
│   ├── timefit.py              # Thuật toán tính tốc độ đọc, co giãn khoảng lặng giữa các câu
│   ├── audio_track.py          # Render clip âm thanh, ghép track WAV, căn WordTiming thực tế
│   ├── mixer.py                # Trộn âm: Force Mix 44.1kHz stereo, ducking sidechaincompress, burn ASS/SRT
│   ├── pipeline.py             # Orchestrator gom toàn bộ quy trình: prepare_script() và render()
│   ├── downloader.py           # Wrapper yt-dlp tải video từ URL (chưa kiểm thử với mạng thật)
│   ├── transcriber.py          # Wrapper faster-whisper nhận diện giọng nói (chưa thử với model nặng thật)
│   ├── separator.py            # Wrapper Demucs tách vocal/nhạc nền (tùy chọn)
│   └── diagnostics.py          # Kiểm tra cấu hình phần cứng/phần mềm
├── gui/                        # [GIAO DIỆN TKINTER - CHỈ GỌI VÀO CORE]
│   ├── app.py                  # Cửa sổ chính Tkinter DubStudio, xử lý Threading an toàn qua Queue (_pump)
│   ├── studio_tab.py           # Tab chính: Chọn video, kịch bản, cấu hình giọng, nút "📂 Mở..."
│   ├── translator_tab.py       # Tab công cụ dịch độc lập
│   ├── downloader_tab.py       # Tab tải video yt-dlp độc lập
│   ├── settings_tab.py         # Tab cấu hình API key, lưu settings.json
│   ├── widgets.py              # Custom UI: GradientProgressBar, LogBox, FileRow, LabeledScale
│   └── theme.py                # Màu sắc, font chữ chuẩn Dark Theme
├── tests/                      # [BỘ KIỂM THỬ TỰ ĐỘNG PYTEST - 26 TESTS PASSING 100%]
│   ├── conftest.py             # Fixture sample_video: tạo video 12s bằng FFmpeg lavfi (testsrc + sine)
│   ├── test_cache.py           # Test cache hit, partial hit, miss on params change, auto regenerate on corrupt
│   ├── test_cli.py             # Test CLI help và lệnh render end-to-end với ToneTTS
│   ├── test_ffmpeg_utils.py    # Test probe duration & audio/video stream
│   ├── test_pipeline.py        # Test pipeline render simple, ducking, flaky clip, video không tiếng
│   ├── test_subtitle_engine.py # Test ToneTTS WordTiming, sinh .ass UTF-8 tag \k, burn .ass lên video fixture
│   ├── test_subtitles.py       # Test parse SRT, bracket format, CSV, time normalize, split segment
│   ├── test_timefit.py         # Test co giãn tốc độ, atempo chain
│   └── test_translator.py      # Test cơ chế fallback model Gemini và bù câu thiếu
├── cli.py                      # Điểm vào CLI: python cli.py render ... & python cli.py clear-cache
├── run.py                      # Điểm vào GUI: python run.py
├── requirements.txt            # Dependencies bắt buộc: pysubs2, requests, google-genai...
├── requirements-dev.txt        # Dependencies test/CI (pytest, imageio-ffmpeg)
├── requirements-optional.txt   # faster-whisper, demucs (tùy chọn)
├── .env.example                # File mẫu API key (GEMINI_API_KEY, ELEVENLABS_API_KEY)
├── .gitignore                  # Chặn .env, cache/* (trừ .gitkeep), output/, temp_processing/, *.exe, media
└── .github/workflows/ci.yml    # CI test tự động trên Ubuntu với FFmpeg
```

---

## 3. Những gì đã hoàn thành trong Phase 2

1. **Hệ thống Cache audio thô & metadata (`core/cache.py`):**
   - Băm SHA-256 từ `(schema_version, engine, voice_id, engine_params, text)`. Không chứa `speed_rate` để tối ưu tái sử dụng audio thô.
   - Lưu cặp `<hash><ext>` và `<hash>.json` chứa mốc `WordTiming`.
   - Ghi nguyên tử qua `.tmp` và `os.replace()`.
   - Kiểm tra toàn vẹn file bằng `ffprobe` (tự động phát hiện và tạo lại nếu file rỗng 0 byte hoặc hỏng).
   - Lệnh `python cli.py clear-cache` dọn sạch cache nhưng bảo toàn file `cache/.gitkeep`.

2. **Chuẩn hóa mô hình dữ liệu (`core/models.py`):**
   - Dataclass `WordTiming(word: str, start: float, end: float)` có hàm `scale_and_shift(speed, offset)`.
   - Mở rộng `Segment` với trường `words: list[WordTiming] = field(default_factory=list)`.
   - Cung cấp `scale_word_timings` tự động quy đổi mốc từ khi audio bị co giãn tốc độ và ghép vào timeline video.

3. **TTS tích hợp WordTiming (`core/tts.py` & `core/audio_track.py`):**
   - `EdgeTTS`: Sử dụng `Communicate(..., boundary="WordBoundary")`, đọc luồng `stream()` và chuyển đổi offset từ 100ns sang giây.
   - `ElevenLabsTTS`: Endpoint `/v1/text-to-speech/{voice_id}/with-timestamps` và gom ký tự thành từ.
   - `ToneTTS`: Tự sinh WordTiming giả lập tỉ lệ theo từ vựng cho kiểm thử offline.
   - Tích hợp `AudioCache` vào `render_clips()` và `DubbingPipeline`.

4. **Engine Phụ đề động Karaoke ASS (`core/subtitle_engine.py`):**
   - Sử dụng `pysubs2` sinh `.ass` với tag `\kf` hoặc `\k` đổi màu highlight theo từng từ.
   - Lưu file tường minh bằng `encoding="utf-8"` (chống lỗi cp1258 trên Windows).
   - Hàm `burn_ass_subtitles()` và tích hợp vào `core/mixer.py` chạy FFmpeg qua thư mục làm việc tương đối để không bị lỗi thoát ký tự `C:\...` trên Windows.

5. **Bộ kiểm thử tự động offline (26/26 tests passed 100%):**
   - `test_cache.py`: 4 test kiểm chứng cache hit (call_count=0), partial hit khi sửa 1 câu (call_count=1), miss khi đổi params, và tự phục hồi khi cache rỗng/hỏng.
   - `test_subtitle_engine.py`: 3 test kiểm tra ToneTTS sinh WordTiming, sinh file ASS UTF-8 có tag `\k`, và burn phụ đề ASS lên fixture video thoát mã 0.

6. **Chỉnh sửa GUI & Documentation:**
   - Đổi `APP_NAME` thành `DubStudio` (hiển thị trên tiêu đề cửa sổ GUI và CLI).
   - Trong `gui/studio_tab.py`: Nút mở đổi nhãn thành `📂 Mở...` kèm chiều rộng cố định, không bị cắt chữ.
   - Cập nhật badge CI GitHub Actions và chi tiết tiến độ Phase 2 trong `README.md`.

---

## 4. Hướng dẫn cho kiểm thử thực tế & Phase 3

### Các mục cần kiểm thử thủ công (yêu cầu mạng thật & API key thật):
- **ElevenLabs `/with-timestamps`:** Cần key thật để xác thực độ chính xác của endpoint căn ký tự.
- **Edge-TTS `WordBoundary`:** Cần kết nối mạng để nghe giọng đọc và xem chữ nhảy màu karaoke thực tế trên video dài.
- **Giao diện người dùng:** Chạy `python run.py` để kiểm tra trực quan nút `📂 Mở...` và tiêu đề cửa sổ.
