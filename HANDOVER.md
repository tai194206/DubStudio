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
├── tests/                      # [BỘ KIỂM THỬ TỰ ĐỘNG PYTEST - 35 TESTS PASSING 100%]
│   ├── conftest.py             # Fixture sample_video: tạo video 12s bằng FFmpeg lavfi (testsrc + sine)
│   ├── test_cache.py           # Test cache hit, partial hit, miss on params change, auto regenerate on corrupt
│   ├── test_cli.py             # Test CLI help và lệnh render end-to-end với ToneTTS
│   ├── test_ffmpeg_utils.py    # Test probe duration & audio/video stream
│   ├── test_pipeline.py        # Test pipeline render simple, ducking, flaky clip, video không tiếng
│   ├── test_subtitle_engine.py # Test ToneTTS WordTiming, sinh .ass UTF-8 tag \k, burn .ass lên video fixture
│   ├── test_subtitles.py       # Test parse SRT, bracket format, CSV, time normalize, split segment
│   ├── test_timefit.py         # Test co giãn tốc độ, atempo chain
│   ├── test_transcriber.py     # Test đọc WAV 16kHz mono float32 và kiểm tra lỗi định dạng âm thanh
│   └── test_translator.py      # Test fallback model 404/403, báo lỗi khi hết model, xưng hô, resolve_model
├── cli.py                      # Điểm vào CLI: python cli.py render ... & python cli.py clear-cache
├── run.py                      # Điểm vào GUI: python run.py
├── requirements.txt            # Dependencies bắt buộc: pysubs2, requests, google-genai, numpy...
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

5. **Bộ kiểm thử tự động offline (35/35 tests passed 100%):**
   - `test_cache.py`: 4 test kiểm chứng cache hit, partial hit, miss on params change, auto regenerate on corrupt/empty.
   - `test_subtitle_engine.py`: 3 test kiểm tra ToneTTS sinh WordTiming, sinh file ASS UTF-8 có tag `\k`, và burn phụ đề ASS lên fixture video.
   - `test_transcriber.py`: 4 test kiểm tra đọc WAV 16kHz mono float32 hợp lệ, bắt lỗi sai framerate, stereo, hoặc sai sample width.
   - `test_translator.py`: 6 test kiểm tra fallback 404, fallback 403 / permission denied, báo lỗi `DubberError` khi toàn bộ model chết (không lặp vô hạn), bù câu thiếu, quy tắc xưng hô `pronoun_rules`, và `resolve_model` tự động chọn model mới nhất theo task profile.

6. **Style Presets, Task Profiles & Pronoun Rules:**
   - `core/style_presets.py`: Danh sách phong cách mẫu (`STYLE_PRESETS`), fallback models Gemini và quy tắc lọc model theo mức độ công việc (`TASK_PROFILES`: Nhanh, Cân bằng, Phức tạp).
   - `core/config.py`: Tích hợp `task_profile`, `style_preset`, `style`, `pronoun_rules` vào `DubSettings`.
   - `core/translator.py` & `core/pipeline.py`:
     - Hàm `resolve_model(profile_name, available)` chọn model mới nhất phù hợp quy tắc include/exclude.
     - Xử lý lỗi 403 / permission / forbidden để tự động chuyển sang model dự phòng khác; nếu toàn bộ model đều chết thì raise `DubberError`.
     - Đưa điều kiện xưng hô (`pronoun_rules`) vào `_system_prompt` và payload gửi sang Gemini API.
   - `gui/studio_tab.py` & `gui/translator_tab.py`: Đồng bộ các combobox mức độ việc, chọn phong cách mẫu, và ô nhập xưng hô.

7. **Xử lý âm thanh Whisper an toàn (Bypass PyAV DLL error trên Windows):**
   - `core/transcriber.py`: `_read_wav_float32(path)` đọc file WAV 16-bit mono 16kHz thành mảng `numpy.float32` [-1, 1], lazy import `numpy` bên trong hàm, không type hint `-> np.ndarray` ở chữ ký, và truyền trực tiếp vào `WhisperModel.transcribe(audio, ...)` giúp loại bỏ hoàn toàn lỗi crash thư viện PyAV trên môi trường Windows.

8. **Chỉnh sửa GUI & Documentation:**
   - Đổi `APP_NAME` thành `DubStudio` (hiển thị trên tiêu đề cửa sổ GUI và CLI).
   - Trong `gui/studio_tab.py`: Nút mở đổi nhãn thành `📂 Mở...` kèm chiều rộng cố định, không bị cắt chữ.
   - Cập nhật tài liệu phân định rõ kiểm thử tự động, kiểm thử thủ công và phần chưa kiểm chứng.

---

## 4. Hướng dẫn cho kiểm thử thực tế & Phase 3

### Các mục cần kiểm thử thủ công (yêu cầu mạng thật & API key thật):
- **faster-whisper trên Windows:** Chạy "① Tạo kịch bản" trên video thật để xác thực việc đọc WAV trực tiếp và sinh timeline phụ đề.
- **Dịch Gemini với API Key thật:** Bấm "Làm mới" danh sách model từ API, kiểm tra combobox "Mức độ việc" tự chọn model `flash` hoặc `pro`, và kiểm tra chất lượng dịch tuân thủ `pronoun_rules`.
- **ElevenLabs `/with-timestamps`:** Cần key thật để xác thực độ chính xác của endpoint căn ký tự.
- **Edge-TTS `WordBoundary`:** Cần kết nối mạng để nghe giọng đọc và xem chữ nhảy màu karaoke thực tế trên video dài.
- **Giao diện người dùng:** Chạy `python run.py` để kiểm tra trực quan nút `📂 Mở...` và tiêu đề cửa sổ.

---

## 5. Lịch sử thay đổi & Các quyết định kiến trúc quan trọng

Để các kỹ sư / AI Agent tiếp quản sau này không lặp lại sai lầm, dưới đây là lý do cốt lõi của các quyết định kỹ thuật:

1. **Vì sao loại bỏ `speed_rate` khỏi khóa cache audio (`core/cache.py`):**
   - Âm thanh sinh ra từ TTS engine phụ thuộc vào text và voice params, không phụ thuộc vào tốc độ co giãn của timeline video.
   - Tốc độ tăng tốc (`atempo`) được áp dụng *sau* khi lấy audio thô từ cache.
   - Nếu đưa `speed_rate` vào khóa cache, chỉ cần người dùng thay đổi timeline video một chút hoặc tinh chỉnh `max_speed` thì toàn bộ cache sẽ bị miss, gây tốn quota API vô ích.

2. **Vì sao không hardcode tên model Gemini (`core/style_presets.py`, `core/translator.py`):**
   - Google cập nhật và deprecated các model Gemini rất thường xuyên (ví dụ: `gemini-1.5-*`, `gemini-2.0-*`, `gemini-2.5-*`... thay nhau ra mắt).
   - Danh sách model được cấp phép khác nhau giữa các API key (nhiều key bị 403 Forbidden với một số model).
   - Do đó, `FALLBACK_MODELS` chỉ dùng khi chưa có kết nối mạng / chưa có key; khi có key, nút "Làm mới" sẽ fetch danh sách thực tế từ API và `resolve_model` sẽ tìm model mới nhất khớp với `TASK_PROFILES` dựa trên quy tắc `include` / `exclude`.

3. **Vì sao lazy import `numpy` trong `_read_wav_float32` (`core/transcriber.py`):**
   - `core/transcriber.py` được import ở nhiều nơi (kể cả khi khởi chạy GUI hoặc CLI không dùng tính năng transcribe).
   - Đặt `import numpy as np` bên trong hàm đọc WAV giúp module `transcriber` load cực nhanh, không kéo `numpy` vào bộ nhớ khi chỉ khởi tạo hoặc khi chạy các lệnh kiểm thử khác. Không dùng `-> np.ndarray` ở chữ ký hàm để tránh lỗi NameError khi numpy chưa được import ở module-level.

4. **Vì sao đọc WAV bằng `wave` và truyền mảng float32 vào Whisper thay vì truyền đường dẫn file:**
   - `faster-whisper` khi nhận đường dẫn file âm thanh sẽ dùng thư viện `PyAV` (FFmpeg C binding) để decode. Trên Windows, `PyAV` thường xuyên gặp lỗi thiếu DLL runtime, crash tiến trình hoặc xung đột build.
   - Sử dụng thư viện chuẩn `wave` của Python để đọc mẫu âm thanh 16-bit PCM rồi chuẩn hóa sang mảng `float32` [-1.0, 1.0] bypass hoàn toàn `PyAV`, chạy ổn định 100% trên mọi bản Windows.

5. **Vì sao burn phụ đề ASS dùng đường dẫn tương đối trong `core/mixer.py`:**
   - Filter `ass=filename` của FFmpeg trên Windows bị lỗi cú pháp khi đường dẫn chứa dấu hai chấm của ổ đĩa (ví dụ: `C:\path\to\sub.ass` bị FFmpeg parse thành protocol hoặc escape sai).
   - Đặt working directory của tiến trình FFmpeg tại thư mục chứa file và chỉ truyền tên file tương đối `ass=sub.ass` giúp loại bỏ hoàn toàn lỗi này trên cả Windows và Ubuntu CI.
