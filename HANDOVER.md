# TÀI LIỆU BÀN GIAO KỸ THUẬT (AI HANDOVER DOCUMENT)
**Dự án**: DubStudio - Story Mode v2 & TTS Pipeline  
**Ngày cập nhật**: 17/11/2026  
**Môi trường phát triển**: Windows (PowerShell), Python 3.12 (.venv), FFmpeg 7.x, Git

---

## 1. TỔNG QUAN HỆ THỐNG VÀ KIẾN TRÚC

### 1.1 Mục tiêu dự án
Tích hợp **Story Mode v2** vào hệ sinh thái DubStudio:
- Tạo video ngắn dạng dọc (9:16) từ kịch bản phân cảnh (`storyboard_v2.json`).
- Hỗ trợ cơ chế **Hybrid**: Kết hợp video động (`flow_video` từ Google Flow/Kling/Runway) cho các cảnh đắt giá và ảnh tĩnh kèm hiệu ứng Ken Burns chuyển động mượt (`static_image` từ Kaggle/SD) cho các cảnh thông thường.
- Lồng tiếng tự động qua **Edge-TTS / ElevenLabs** kèm căn chỉnh mốc thời gian từng từ (Word-level timing alignment) để sinh phụ đề Karaoke ASS chính xác theo nhịp đọc.
- Tự động ghép nối video, nhạc nền (BGM với Ducking), hiệu ứng âm thanh (SFX), và render hoàn chỉnh bằng FFmpeg.

### 1.2 Nguyên tắc thiết kế cốt lõi
1. **Phân tách tuyệt đối giữa Core và GUI**:
   - Thư mục `core/` là headless backend (CLI / Automation / Unit Test), **tuyệt đối không bao giờ import `tkinter` hoặc `gui/`**.
   - `gui/` (như `gui/story_tab.py`, `gui/studio_tab.py`) chỉ đóng vai trò View/Controller, gọi `core/` thông qua Background Thread để tránh block giao diện.
2. **Kiểm thử độc lập (Offline-first)**:
   - Toàn bộ test suite (178 test) được thiết kế mock mạng và GPU, có thể chạy 100% offline không cần live internet.

---

## 2. NHỮNG CÔNG VIỆC VÀ CẢI TIẾN ĐÃ THỰC HIỆN

### 2.1 Cải tiến Edge-TTS: Cơ chế chịu lỗi kép (Dual-Path Fallback)
- **Vấn đề phát hiện**: Với các câu dài hoặc khi kết nối mạng chập chờn, Edge-TTS WebSocket `stream(boundary="WordBoundary")` thỉnh thoảng bị ngắt kết nối đột ngột giữa chừng (`NoAudioReceived`) hoặc trả về audio bị cắt cụt (truncated).
- **Giải pháp (`core/tts.py` & `tts_fix2`)**:
  - Tích hợp kiểm tra tính toàn vẹn thời lượng âm thanh (`check_edge_integrity(audio_bytes, last_boundary_end, tolerance=0.25)`).
  - Áp dụng cơ chế Retry 3 lần cho luồng `stream()`.
  - Nếu cả 3 lần `stream()` đều hỏng hoặc cụt: Tự động fallback sang gọi `edge_tts.Communicate.save()` (phương thức HTTP tải toàn bộ audio ổn định).
  - Khi chạy fallback qua `save()`, module sẽ tự động nội suy mốc từ (`timing_source = "estimated"`) dựa theo độ dài ký tự từng từ trên tổng thời lượng audio đo bằng `ffprobe`.
  - Ghi nhận cờ `last_timing_source` (`"boundary"` hoặc `"estimated"`).
  - Bổ sung bộ 15 unit tests chuyên biệt trong `tests/test_tts_integrity.py`.

### 2.2 Sửa lỗi ASS Subtitle 60-Second Rollover
- **File**: `core/elevenlabs_alignment.py`
- **Lỗi**: Hàm `_format_ass_time(seconds)` khi gặp giây thứ `59.999` làm tròn thành `0:00:60.00`, vi phạm chuẩn thời gian định dạng ASS (`h:mm:ss.cc`), gây lỗi crash hoặc lệch phụ đề ở trình render video.
- **Khắc phục**: Chuyển đổi toàn bộ thời gian sang centiseconds tích lũy `total_cs = max(0, int(round(seconds * 100)))` rồi mới chia lấy dư cho 6000 cs (phút) và 360000 cs (giờ). Đảm bảo `59.999s` $\rightarrow$ `0:01:00.00`.

### 2.3 Chuẩn hóa FFmpeg Filter Chain (Khắc phục lỗi Concat Stream)
- **File**: `core/story_pipeline.py`
- **Vấn đề**: Khi nối (concat) giữa clip video động (`flow_video`) và clip ảnh Ken Burns (`static_image`), nếu Sample Aspect Ratio (SAR), FPS hoặc định dạng màu bị lệch, FFmpeg concat demuxer sẽ sinh lỗi giật hình, méo khung hoặc lỗi giải mã.
- **Khắc phục**: Chuẩn hóa filter chain đồng nhất cho cả 2 loại asset:
  ```text
  scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p
  ```

### 2.4 Bắt lỗi Schema nghiêm ngặt (Strict Schema Validation)
- **File**: `core/story_schema.py`
- **Thay đổi**: Thay vì âm thầm gán giá trị mặc định (`zoom_in`/`none`) khi kịch bản chứa `motion` hoặc `sfx` không hợp lệ, hệ thống sẽ ném ngoại lệ rõ ràng (`DubberError`) để phát hiện ngay lỗi sai trong kịch bản storyboard.

### 2.5 Cấu hình Pytest & Sửa lỗi Namespace Collision
- **File**: `pytest.ini`
- **Vấn đề**: Repo có cả thư mục `tests/` ở root và `story_mode/tests/`, dẫn đến đụng độ namespace module `tests.*` khi pytest quét test đồng thời.
- **Khắc phục**: Tạo file `pytest.ini` kích hoạt chế độ `--import-mode=importlib`:
  ```ini
  [pytest]
  pythonpath = . story_mode
  testpaths = tests story_mode/tests kaggle/tests
  python_files = test_*.py
  addopts = --import-mode=importlib
  ```
- **Kết quả**: Toàn bộ **178 tests** chạy đồng thời vượt qua 100% trong ~57s.

### 2.6 Sửa lỗi đường dẫn Windows trong Story Mode v2
- **File**: `story_mode/tests/test_v2.py`
- **Lỗi**: Sử dụng đường dẫn Unix cứng `/tmp/none` gây lỗi `FileNotFoundError: [WinError 3]` trên Windows.
- **Khắc phục**: Chuyển thành `Path(tempfile.gettempdir()) / "none"`.

### 2.7 Chuẩn hóa `.gitignore`
- **Vấn đề**: Quy tắc cũ bỏ qua cả thư mục `story_mode/`, khiến code nguồn bị git bỏ rơi.
- **Khắc phục**: Chỉ bỏ qua cache và artifact sinh ra trong quá trình chạy:
  ```gitignore
  story_mode/projects/*/output/
  story_mode/projects/*/scenes/
  story_mode/cache/
  cache/*
  !cache/.gitkeep
  ```

---

## 3. CẤU TRÚC THƯ MỤC HIỆN TẠI

```text
DubStudio/
├── .venv/                         # Virtual environment (Python 3.12)
├── core/                          # CORE BACKEND ENGINE (Không GUI, không tkinter)
│   ├── elevenlabs_alignment.py   # Word-level alignment & sinh phụ đề ASS Karaoke
│   ├── story_pipeline.py         # Pipeline render video, Ken Burns, audio ducking
│   ├── story_schema.py           # Pydantic/Data validation cho storyboard v2
│   ├── tts.py                    # Edge-TTS engine (chịu lỗi stream + save fallback)
│   ├── ...                       # Các module core xử lý audio/video khác
├── gui/                           # GIAO DIỆN NGƯỜI DÙNG (Tkinter)
│   ├── app.py                    # Root Window & Navigation Tabs
│   ├── story_tab.py              # Tab giao diện Story Studio v2
│   ├── studio_tab.py             # Tab lồng tiếng chuẩn (Dubbing Studio)
├── story_mode/                    # MODULE STORY MODE & DỰ ÁN
│   ├── projects/
│   │   └── tap_01/               # Dự án mẫu: "Hành trình tìm đường cứu nước 1911"
│   │       ├── storyboard.json   # 28 phân cảnh
│   │       └── scenes/           # 26 ảnh PNG (001.png -> 026.png)
│   ├── story/
│   │   ├── story.py              # Story engine v1
│   │   └── v2.py                 # Story engine v2 (hỗ trợ CLI build & validate)
│   └── tests/                    # 61 tests cho Story Mode
├── kaggle/                        # Tích hợp ASR / Transcription Notebooks (51 tests)
├── tests/                         # 66 tests cho Core DubStudio & TTS Integrity
├── cli.py                         # CLI Interface (e.g. python cli.py clear-cache)
├── pytest.ini                     # Cấu hình test importlib toàn dự án
└── HANDOVER.md                    # Tài liệu này
```

---

## 4. TÌNH TRẠNG HIỆN TẠI & BƯỚC TIẾP THEO

### 4.1 Điểm nghẽn cần lưu ý về Assets của `tap_01`
- File `story_mode/projects/tap_01/storyboard.json` có **28 cảnh** (415 từ).
- Thư mục `story_mode/projects/tap_01/scenes/` hiện có **26 ảnh** (`001.png` đến `026.png`). Kích thước gốc của các ảnh là `768x1344` (đã có bộ lọc FFmpeg tự động upscale & crop sang chuẩn `1080x1920`).
- **Đang thiếu ảnh cho cảnh 27 và cảnh 28**:
  - *Cảnh 27*: Bác Hồ đứng trước lá cờ đỏ sao vàng.
  - *Cảnh 28*: Hình bóng Bác Hồ hướng về tương lai.

### 4.2 Các lệnh kiểm tra nhanh dành cho AI/Dev kế nhiệm
1. **Chạy toàn bộ test suite (Offline)**:
   ```powershell
   .venv\Scripts\pytest -q
   # Kết quả chuẩn: 178 passed
   ```
2. **Kiểm tra TTS Integrity tests**:
   ```powershell
   .venv\Scripts\pytest tests\test_tts_integrity.py -v
   # Kết quả chuẩn: 15 passed
   ```
3. **Xóa cache giọng đọc**:
   ```powershell
   .venv\Scripts\python cli.py clear-cache
   ```
4. **Kiểm tra Validation Storyboard**:
   - Đối với kịch bản chuẩn v1 (ví dụ `tap_01`):
     ```powershell
     $env:PYTHONPATH="story_mode"; .venv\Scripts\python -m story validate story_mode\projects\tap_01\storyboard.json
     ```
   - Đối với kịch bản chuẩn v2 Hybrid (ví dụ `ep02_sample_v2`):
     ```powershell
     $env:PYTHONPATH="story_mode"; .venv\Scripts\python -m story.v2 validate story_mode\projects\ep02_sample_v2\storyboard_v2.json
     ```

### 4.3 Điểm nghẽn cần lưu ý về Assets của `tap_01`
- File `story_mode/projects/tap_01/storyboard.json` là định dạng v1 có **28 cảnh** (415 từ).
- Thư mục `story_mode/projects/tap_01/scenes/` hiện có **26 ảnh** (`001.png` đến `026.png`). Kích thước gốc của các ảnh là `768x1344` (đã có bộ lọc FFmpeg tự động upscale & crop sang chuẩn `1080x1920`).
- **Đang thiếu ảnh cho cảnh 27 và cảnh 28**:
  - *Cảnh 27*: Bác Hồ đứng trước lá cờ đỏ sao vàng.
  - *Cảnh 28*: Hình bóng Bác Hồ hướng về tương lai.
- Thư mục `story_mode/projects/ep02_sample_v2/storyboard_v2.json` là định dạng v2 Hybrid hỗ trợ cả `flow_video` và `static_image`.

### 4.4 Nhiệm vụ kế tiếp cần triển khai
1. Quyết định bổ sung 2 ảnh còn thiếu (`027.png`, `028.png`) cho `tap_01` hoặc nhân bản tạm `026.png` làm placeholder.
2. Thực hiện render mẫu hoàn chỉnh tập `tap_01` (End-to-End Build) để kiểm tra chất lượng video đầu ra, căn khớp nhịp Karaoke và hiệu ứng âm thanh SFX/BGM.

