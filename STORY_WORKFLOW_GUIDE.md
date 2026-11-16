# 🎬 QUY TRÌNH SẢN XUẤT VIDEO LỊCH SỬ AI (INPUT $\rightarrow$ OUTPUT)

Tài liệu hướng dẫn quy trình khép kín từ lúc lên ý tưởng đến khi xuất file `.mp4` đăng TikTok/Shorts (khung dọc **9:16**, thời lượng **2–3 phút**, dung lượng tối ưu **12–18MB**) bằng cách kết hợp **Gemini**, **Kaggle GPU T4**, và **DubStudio**.

---

## 1. Kiến trúc dây chuyền End-to-End

Thay vì copy-paste thủ công từng cảnh lên web rồi kéo thả trên CapCut, toàn bộ quy trình được chia thành 5 trạm bán tự động:

```text
[1. Ý TƯỞNG & KỊCH BẢN] (Gemini Flash)
         │  -> script.txt (450 - 530 từ tiếng Việt)
         ▼
[2. PHÂN CẢNH STORYBOARD] (Gemini Flash)
         │  -> storyboard.json (25 - 35 cảnh + image_prompt + motion + sfx)
         ▼
[3. VẼ ẢNH HÀNG LOẠT] (Kaggle GPU T4 - Flux.1-schnell / SDXL)
         │  -> scenes.zip (001.png ... 030.png)
         │  (Tùy chọn: Thay 3-5 cảnh Hook/Cao trào bằng clip .mp4 từ Google Flow)
         ▼
[4. DỰNG PHIM TỰ ĐỘNG] (DubStudio Phase 3 - Local / Kaggle)
         │  -> TTS + Hash Cache -> Ken Burns khớp giọng -> BGM Ducking + SFX -> Sub Karaoke .ass
         ▼
[5. XUẤT FILE TỐI ƯU] (FFmpeg 1080x1920, 30fps, CRF 24)
         │  -> final_ep01.mp4 (~12 - 18MB, sẵn sàng đăng TikTok)
```

---

## 2. Cấu trúc thư mục chuẩn cho mỗi tập video

Để DubStudio đọc tự động không cần chọn từng file lẻ, mỗi tập nên gom vào 1 thư mục theo chuẩn sau:

```text
projects/
└── ep01_alan_turing/
    ├── script.txt            # Kịch bản lời đọc đã duyệt tay
    ├── storyboard.json       # File điều phối toàn bộ cảnh, prompt, chuyển động, SFX
    ├── assets/               # Giải nén từ scenes.zip tải về từ Kaggle
    │   ├── 001.mp4           # (Tùy chọn) Clip động cho cảnh Hook mở đầu
    │   ├── 002.png           # Ảnh tĩnh 2D người que (DubStudio tự áp Ken Burns)
    │   ├── 003.png
    │   └── ...
    └── output/
        ├── ep01_karaoke.ass  # File phụ đề vùng an toàn 9:16
        └── ep01_final.mp4    # Thành phẩm cuối cùng (~15MB)
```

> **Quy tắc ưu tiên media:** Với cảnh số `N`, DubStudio sẽ tự tìm file `00N.mp4` trước; nếu không có mới lấy `00N.png` (hoặc `.jpg`) rồi áp hiệu ứng Ken Burns. Cách này giúp bạn áp dụng chiến lược **80/20** cực nhàn: 80% cảnh dùng ảnh tĩnh vẽ miễn phí trên Kaggle, chỉ 20% cảnh đắt giá nhất (Hook 10s đầu & Cao trào) mới cần thay bằng clip AI động.

---

## 3. Hướng dẫn chi tiết 5 Bước (Input $\rightarrow$ Output)

### Bước 1: Chốt ý tưởng & Viết kịch bản (Gemini Flash)
- **Input:** Chủ đề lịch sử AI (ví dụ: *Alan Turing*, *Mùa đông AI*, *Nước cờ 37 của AlphaGo*, *Bài báo Transformer*).
- **Thao tác:**
  1. Dùng **Prompt 1** (trong `docs/STORY_PROMPTS.md`) trên dòng **Gemini Flash** (nhanh, tiết kiệm quota, không cần bản Pro) để chọn ra 1 góc khai thác kịch tính.
  2. Dùng **Prompt 2** để viết kịch bản dài **450–530 từ tiếng Việt** theo cấu trúc 4 hồi:
     - **0s – 10s (Hook):** Đi thẳng vào mâu thuẫn/sự thật gây sốc, không chào hỏi.
     - **10s – 40s (Bối cảnh):** Giới thiệu nhanh nhân vật và thời điểm.
     - **40s – 105s (Cao trào + Mid-Hook):** Đẩy kịch tính, chèn 2 câu giữ chân giữa video.
     - **105s – 150s (Cú twist & Kết):** Liên hệ hiện tại + 1 câu hỏi ngắn kéo bình luận.
  3. **Duyệt tay 1 phút:** Đọc nhẩm thành tiếng để sửa các câu quá dài (>20 từ) hoặc từ tiếng Anh khó đọc cho TTS tiếng Việt.
- **Output:** File `script.txt`.

---

### Bước 2: Tạo `storyboard.json` (Gemini Flash)
- **Input:** Nội dung `script.txt`.
- **Thao tác:** Dán vào **Prompt 3** để AI tự cắt thành **25–35 cảnh** (mỗi cảnh 1–2 câu thoại, dài khoảng 4–6 giây) và xuất ra đúng 1 khối JSON duy nhất.
- **Cấu trúc chuẩn của `storyboard.json`:**
```json
{
  "title": "Người đàn ông tạo ra AI",
  "aspect_ratio": "9:16",
  "style_suffix": "2D flat vector cartoon illustration, OverSimplified style, cute round white stick-figure characters with simple expressive faces, bold dark outlines, solid flat colors, clean minimalist background, vertical 9:16 composition, no text, no watermark",
  "bgm": "assets_global/bgm/mystery_history.mp3",
  "scenes": [
    {
      "id": 1,
      "speech": "Năm 1950, một thiên tài đặt ra câu hỏi khiến cả thế giới run sợ.",
      "image_prompt": "Close-up of a round white stick-figure scientist in a 1950s tweed suit looking intensely at a giant glowing vintage computer, dark blue background",
      "motion": "zoom_in",
      "sfx": "boom",
      "on_screen_text": "NĂM 1950"
    }
  ]
}
```
- **Lưu ý kiểm tra nhanh:** Đảm bảo các cảnh liền kề có đảo góc máy (`close-up` $\rightarrow$ `wide shot` $\rightarrow$ `diagram/map`) và không chèn chữ vào `image_prompt` (chữ nổi bật sẽ để DubStudio vẽ bằng FFmpeg/ASS từ trường `on_screen_text` để không bị lỗi chính tả tiếng Việt).
- **Output:** File `storyboard.json`.

---

### Bước 3: Vẽ ảnh hàng loạt trên Kaggle GPU T4
- **Input:** Upload `storyboard.json` vào mục **Input** của Notebook Kaggle (bật Accelerator: **GPU T4**).
- **Thao tác:**
  1. Notebook đọc `storyboard.json`, tự động nối `image_prompt + ", " + style_suffix` cho từng cảnh.
  2. Chạy vòng lặp qua model vẽ ảnh mở tốc độ cao (như **Flux.1-schnell** hoặc **SDXL Turbo / Lightning** với khung dọc `768x1344` hoặc `832x1472` rồi upscale nhẹ lên `1080x1920`).
  3. Lưu từng ảnh theo tên chuẩn `001.png`, `002.png`... vào `/kaggle/working/scenes/` và nén thành `/kaggle/working/scenes.zip`.
  4. *(Tùy chọn nâng cấp chất lượng)*: Với 3–5 cảnh quan trọng nhất (như cảnh `001`), có thể ném ảnh lên Google Flow / công cụ Image-to-Video để tạo clip động 4–5 giây, lưu đè thành `001.mp4` trong thư mục `assets/`.
- **Output:** Tải `scenes.zip` từ `/kaggle/working` và giải nén vào `projects/<tên_tập>/assets/`.

---

### Bước 4: Dựng & Render tự động bằng DubStudio (Phase 3)
- **Input:** Chọn `storyboard.json` + thư mục `assets/` trên DubStudio (CLI hoặc tab Story Studio).
- **Quy trình lõi `core/` tự động xử lý:**
  1. **Sinh giọng đọc + Hash Cache (`core/cache.py`):**
     - Gọi TTS engine (Vbee / Fish Audio / ElevenLabs / Edge-TTS) cho từng `scene["speech"]`.
     - Nhờ cache theo SHA-256, nếu xem xong thấy 1 câu đọc chưa ưng và sửa lại chữ trong `storyboard.json`, lần render sau **chỉ tạo lại đúng 1 câu đó**.
     - Áp tốc độ đọc kể chuyện chuẩn TikTok (`1.15x – 1.20x`), chèn khoảng nghỉ thở tự nhiên `0.15s – 0.20s` giữa các cảnh.
  2. **Khớp hình ảnh & Ken Burns (`core/shorts_builder.py`):**
     - Đo chính xác thời lượng audio của từng cảnh `d_i` giây.
     - Nếu là `.png`: áp FFmpeg `zoompan` theo đúng trường `motion` (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`) dài đúng `d_i` giây.
     - Nếu là `.mp4`: tự cắt hoặc lặp mượt vừa khít `d_i` giây.
  3. **Trộn âm thanh (BGM Ducking + SFX):**
     - Lặp nhạc nền ở mức âm lượng `10% – 15%`, áp `sidechaincompress` tự nhỏ xuống khi có giọng đọc.
     - Chèn hiệu ứng âm thanh ngắn (`boom`, `whoosh`, `pop`...) đúng tại điểm bắt đầu của cảnh có gắn `sfx`.
  4. **Phụ đề Karaoke chuẩn khung dọc 9:16 (`core/subtitle_engine.py`):**
     - Thiết lập `PlayResX=1080, PlayResY=1920`, tự động ngắt cụm ngắn **3–5 từ/dòng** để chữ to, dễ đọc trên điện thoại.
     - Đẩy `MarginV` lên vùng an toàn (**380–450px tính từ đáy**) để tránh bị mô tả video và cụm nút tương tác bên phải của TikTok che mất chữ.
     - Dùng font hỗ trợ tiếng Việt đầy đủ, viền đen dày + bóng đổ rõ nét.
- **Output:** File `ep01_karaoke.ass` và bản dựng hoàn chỉnh.

---

### Bước 5: Xuất file nén tối ưu & Kiểm duyệt trước khi đăng
- **Thông số FFmpeg bắt buộc (giảm dung lượng từ ~70MB xuống ~12–18MB cho clip 2–3 phút):**
  ```text
  -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
  -r 30
  -c:v libx264 -crf 24 -preset slow -pix_fmt yuv420p
  -c:a aac -b:a 128k -ar 44100
  -movflags +faststart
  ```
  - *Vì sao nhẹ mà vẫn nét?* Phong cách hoạt hình 2D màu phẳng có rất ít nhiễu hạt; hạ từ `60fps` xuống `30fps` và dùng `-crf 24 -preset slow` cắt hơn 70% dung lượng thừa mà chữ và nét viền vẫn sắc nét trên màn hình điện thoại.

- **Checklist kiểm duyệt 30 giây trước khi up TikTok:**
  - [ ] 3 giây đầu có hình ảnh chuyển động + chữ nổi bật + câu Hook đọc dứt khoát không?
  - [ ] Phụ đề karaoke tiếng Việt có bị lỗi dấu, tràn lề ngang hay rơi xuống vùng bị che của TikTok không?
  - [ ] Nhạc nền và SFX có bị to quá lấn át giọng đọc không?
  - [ ] Dung lượng file có gọn nhẹ (dưới 25MB cho clip 2–3 phút) và phát mượt từ đầu đến cuối không?

---

## 4. Lộ trình tích hợp tiếp theo

1. **Tích hợp bộ code Kaggle (.zip):** Gộp notebook tách kịch bản (`faster-whisper large-v3`) và notebook vẽ ảnh hàng loạt (`storyboard.json` $\rightarrow$ `scenes.zip`) thành bộ script chuẩn trong thư mục `kaggle/` của repo `DubStudio`.
2. **Triển khai Phase 3 trong `DubStudio`:** Xây dựng `core/shorts_builder.py` (đọc `storyboard.json`, Ken Burns, SFX, BGM ducking, sub 9:16 Safe Zone, preset nén 30fps CRF 24) kèm unit test offline trên CI.
3. **Nâng cấp giọng đọc thực tế:** Cắm thêm adapter TTS tiếng Việt tự nhiên (Vbee AI hoặc Fish Audio) vào `core/tts.py` song song với Edge-TTS và ElevenLabs.