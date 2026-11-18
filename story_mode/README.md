# Story Mode · Video lịch sử AI 9:16

Từ ý tưởng đến file `.mp4` đăng TikTok/Shorts: **Gemini** viết kịch bản và storyboard, **Kaggle T4** vẽ ảnh, **story/** dựng phim tự động.

> Video dựng ra có thể sai sự thật nếu kịch bản sai. Xem mục 7 trước khi đăng.

---

## 1. Quy trình tổng thể

```text
[1] Ý tưởng + kịch bản (Gemini Flash)      -> script.txt (420-550 từ)
[2] Storyboard (Gemini Flash)              -> storyboard.json (25-35 cảnh)
[3] Vẽ ảnh (Kaggle T4, generate_scenes.py) -> scenes.zip (001.png ... NNN.png)
[4] Kiểm tra storyboard (story validate)   -> báo lỗi/cảnh báo trước khi tốn GPU
[5] Dựng phim (story build)                -> giọng đọc + Ken Burns + nhạc + SFX + phụ đề karaoke -> MP4
```

**Chuyển động (zoom/pan) không làm trên Kaggle.** Ảnh vẽ ra là ảnh tĩnh. Chuyển động được áp ở bước 5 bằng FFmpeg `zoompan`, theo trường `motion` của từng cảnh. Nên Kaggle không cần làm motion.

---

## 2. Cấu trúc thư mục

```text
story_mode/
├── kaggle/generate_scenes.py     # script vẽ ảnh (dán vào notebook Kaggle)
├── story/story.py                # kiểm tra + dựng phim
├── story/__main__.py             # chạy: python -m story ...
├── tests/test_story.py           # 31 test offline
├── assets_global/sfx/            # hiệu ứng: whoosh.wav, boom.wav, pop.wav, typing.wav, alarm.wav
├── assets_global/bgm/            # nhạc nền có giấy phép (không tự thêm nhạc không rõ nguồn)
├── projects/ep01_alan_turing/    # mỗi tập một thư mục
│   ├── storyboard.json
│   └── assets/
│       ├── 001.png ... 035.png   # ảnh từ scenes.zip (hoặc 003.mp4 cho cảnh cần clip động)
│       └── audio/                # (tùy chọn) giọng có sẵn: 001.wav sẽ thay TTS cho cảnh 1
├── cache/story/                  # cache giọng đọc (tự tạo, không commit)
└── output/                       # MP4 đầu ra
```

Cài đặt:
```bash
pip install -r requirements.txt   # numpy, edge-tts, pytest
# FFmpeg phải có trong PATH (có libass để đốt phụ đề)
```

---

## 3. Bước 1 · Kịch bản

1. Dùng **Prompt 1** trong `STORY_PROMPTS.md` để chọn chủ đề.
2. Dùng **Prompt 2** để viết kịch bản. Nó đã yêu cầu 4 hồi (Hook / Bối cảnh / Cao trào / Kết).
3. **Đếm từ và đọc to một lần.** Mục tiêu 420–550 từ. Câu nào trên 20 từ thì tách.
4. **Kiểm tra sự thật** (mục 7). Chỗ nào bạn không chắc, bỏ hoặc nói chung hơn.
5. Lưu thành `script.txt`.

---

## 4. Bước 2 · Storyboard

1. Dán `script.txt` vào **Prompt 3**, lưu kết quả thành `projects/<tên_tập>/storyboard.json`.
2. Kiểm tra ngay trên máy:
   ```bash
   python -m story validate projects/ep01_alan_turing/storyboard.json
   ```
   Lỗi cứng (thiếu trường, `motion`/`sfx` sai, trùng `id`) sẽ dừng. Cảnh báo (số từ, số cảnh ngoài khoảng khuyến nghị) chỉ là gợi ý.
3. Đọc lại `image_prompt`:
   - Các cảnh liền kề nên đổi góc máy (close-up, medium, wide, diagram).
   - **Không có chữ trong `image_prompt`.** Chữ hiển thị đi qua `on_screen_text`, được vẽ bằng phụ đề, không do AI vẽ.
   - Mỗi `image_prompt` nên khoảng 30–45 từ tiếng Anh. Prompt dài hơn 77 token sẽ bị CLIP cắt, script Kaggle có cảnh báo khi xảy ra.

> **Đồng bộ phong cách.** `generate_scenes.py` dùng `STYLE_PREFIX` riêng (ngắn, để nằm trong 77 token). `style_suffix` trong `storyboard.json` **không** được đọc tự động. Nếu bạn đổi phong cách, sửa cả hai chỗ.

---

## 5. Bước 3 · Vẽ ảnh trên Kaggle

1. Kaggle → **New Notebook**.
2. **Settings:** Accelerator = **GPU T4 x1** (hoặc P100). Internet = **On** (để tải model SDXL lần đầu).
3. **Add Input → Upload**: đưa `storyboard.json` vào.
4. Dán toàn bộ nội dung `kaggle/generate_scenes.py` vào một ô code rồi **Run**.
5. Theo dõi log. Mỗi cảnh một dòng `✅ Cảnh 007/030 (38.2s)`. Ước tính thời gian: đo trên lần chạy thật, không dựa vào con số ở đây.
6. Khi xong: tải `scenes.zip` ở tab **Output**.
7. Giải nén vào `projects/<tập>/assets/`. Có file `manifest.json` ghi trạng thái từng cảnh.

**Nếu bị hết VRAM hoặc ngắt giữa chừng:** chạy lại notebook. Script bỏ qua ảnh đã có, nên chỉ vẽ phần còn thiếu. Cảnh nào lỗi được ghi trong `failed` của manifest.

**Muốn làm động một cảnh (tùy chọn):** đưa ảnh đó vào công cụ image-to-video (ví dụ Google Flow), tải về, đặt tên `NNN.mp4` trong `assets/`. Builder ưu tiên `.mp4` trước `.png`, và tự cắt hoặc lặp clip cho khớp thời lượng cảnh.

---

## 6. Bước 4–5 · Dựng phim

### Giọng đọc

| Engine | Dùng khi | Ghi chú |
|---|---|---|
| `--engine tone` | Thử nghiệm, không cần mạng | Giọng sin, chỉ để kiểm tra đồng bộ |
| `--engine edge --voice vi-VN-HoaiMyNeural` | Thử nghiệm với giọng thật | Miễn phí, nhưng không chính thức; có thể bị từ chối khi gửi dày (builder tự thử lại) |
| File có sẵn trong `assets/audio/NNN.wav` | Muốn chất lượng cao hơn | Xuất giọng từ Vbee, Fish Audio, ElevenLabs... rồi đặt tên đúng số cảnh. Cảnh có file này không gọi TTS |

Giọng nào cũng được **cache** theo SHA-256 của (text, engine, voice). Sửa 1 câu trong `storyboard.json` thì lần dựng sau chỉ tạo lại đúng câu đó. Tốc độ `1.15x` áp sau khi cache, nên đổi tốc độ không làm mất cache.

### Dựng

```bash
python -m story build projects/ep01_alan_turing --engine edge --voice vi-VN-HoaiMyNeural
# hoặc thử nhanh:
python -m story build projects/ep01_alan_turing --engine tone --no-subs
```

Builder làm lần lượt:
1. Kiểm tra storyboard và có đủ ảnh cho mọi cảnh không (thiếu thì báo đúng số cảnh, dừng trước khi render).
2. Tạo giọng đọc, cắt khoảng lặng thừa, tăng tốc 1.15x.
3. Mỗi cảnh = thời lượng giọng + 0.2s nghỉ.
4. Dựng clip cho từng cảnh: ảnh theo `motion` (zoom_in, zoom_out, pan_left, pan_right, static) hoặc clip `.mp4`.
5. Ghép track giọng đúng mốc. Trộn BGM (nếu storyboard có trường `bgm`), tự hạ khi có giọng (sidechain). Chèn SFX đúng đầu cảnh.
6. Tạo phụ đề karaoke `.ass`: 3–5 từ mỗi dòng, tô màu theo nhịp, đặt ở vùng an toàn cách đáy 400px. `on_screen_text` hiện đầu cảnh ở trên cùng trong 3 giây.
7. Xuất MP4: 1080x1920, 30fps, H.264 CRF 24 preset slow, AAC 128k, `+faststart`.

Output mặc định: `output/<tên thư mục dự án>.mp4` (tính từ thư mục bạn đứng khi chạy), hoặc chỉ định bằng `--out`. Thêm `--keep-temp` để xem file trung gian (giọng, track, phụ đề).

### Nhạc nền và hiệu ứng

- **BGM:** thêm trường `"bgm": "assets_global/bgm/ten_file.mp3"` trong storyboard. Đường dẫn tương đối với thư mục dự án hoặc gốc repo. Nhạc phải có giấy phép (xem mục 8).
- **SFX:** đặt file `whoosh.wav`, `boom.wav`, `pop.wav`, `typing.wav`, `alarm.wav` vào `assets_global/sfx/`. Cảnh có `sfx` tương ứng sẽ được chèn đúng đầu cảnh. Thiếu file thì builder báo và bỏ qua hiệu ứng đó.
- File `boom.wav` trong repo do chính builder tạo (sóng sin suy giảm). Có thể thay bằng SFX bạn có quyền dùng.

---

## 7. Kiểm duyệt trước khi đăng

### Kiểm tra sự thật (quan trọng nhất)

Các hook kịch tính dễ bị dựng quá tay. Trước khi đăng, kiểm tra từng mốc có năm, tên và con số:
- Mô tả chi tiết về sự kiện gần đây (ví dụ chuyện hội đồng quản trị OpenAI năm 2023, các ngày cụ thể, cách thông báo) phải đối chiếu với nguồn báo chí. Dễ nhớ sai, và đừng để câu hook nói quá hơn nguồn.
- Các câu kiểu "viết xong rồi bỏ đi hết" hoặc "không ai ngờ" là cách kể. Nếu đó là nhận định của bạn, hãy nói rõ hoặc bỏ.
- Với người thật (Turing, Kasparov, Lee Sedol, Hinton, Altman...), **không** vẽ họ làm việc gì mà không có cơ sở. Dùng nhân vật người que chung, và nói rõ đây là dựng lại để kể chuyện.

### Kiểm tra kỹ thuật (30 giây)

- [ ] 3 giây đầu có chuyển động + câu hook rõ.
- [ ] Phụ đề không bị lỗi dấu, không tràn lề, không nằm dưới vùng TikTok che (400px đáy).
- [ ] Giọng đọc không bị hụt, không bị cắt đầu câu, không đè nhạc.
- [ ] Nhạc và SFX không lấn giọng.
- [ ] Dung lượng dưới 25 MB. Nếu lớn hơn, dùng `-crf 26` (đổi trong `story.py`, hàm `build`).

---

## 8. Bản quyền và rủi ro

- **Ảnh SDXL:** model và output có điều khoản riêng. Kiểm tra giấy phép thương mại của checkpoint bạn dùng trước khi bán sản phẩm.
- **Giọng Edge-TTS:** dịch vụ không chính thức, chỉ dùng thử nghiệm. Để đăng kiếm tiền, nên dùng giọng có giấy phép thương mại (Vbee, Fish, ElevenLabs, Azure...).
- **Nhạc nền và SFX:** chỉ dùng nhạc có giấy phép rõ ràng (CC0, thư viện trả phí, hoặc tự sáng tác). Không tải nhạc từ YouTube hay nền tảng khác về dùng.
- **Phong cách:** mô tả phong cách bằng đặc điểm hình ảnh (đầu tròn, viền đậm, màu phẳng), đừng dùng tên kênh cụ thể trong prompt.
- **Nội dung:** đây là kể lại lịch sử bằng lời của bạn. Đừng đọc nguyên văn bài báo hay video khác.

---

## 9. Trạng thái kiểm chứng

| Hạng mục | Trạng thái |
|---|---|
| Kiểm tra storyboard (lỗi cứng, cảnh báo, BOM, JSON hỏng) | ✅ Test tự động |
| Chuyển động Ken Burns (4 kiểu + static) | ✅ Test biểu thức; chạy thật trên ảnh mẫu |
| Cache giọng đọc (trúng cache, sửa 1 câu chỉ tổng hợp lại 1 câu, file hỏng) | ✅ Test tự động |
| Phụ đề karaoke `.ass` (tiếng Việt, tag `\kf`, escape, tiêu đề, định dạng thời gian) | ✅ Test + đã xem khung hình thật |
| Căn mốc giọng đọc vào đúng thời điểm cảnh | ✅ Test tự động (có đột biến bắt được lỗi) |
| Nhánh trộn BGM (sidechain) + SFX | ✅ Test tự động (đã sửa lỗi thật do nhãn filter) |
| Dựng MP4 end-to-end (1080x1920, 30fps, có tiếng và hình) | ✅ Đã chạy thật |
| Đường dẫn BGM kiểu `assets_global/...` | ✅ Test tự động |
| `kaggle/generate_scenes.py` | ⚠️ Chỉ kiểm tra cú pháp. **Chưa chạy** (môi trường kiểm thử không có GPU/torch) |
| Engine `edge` | ❌ Chưa chạy thật (cần mạng); code có thử lại khi lỗi |
| Chất lượng ảnh SDXL, độ nhất quán nhân vật giữa các cảnh | ❌ Chưa đánh giá. Mỗi cảnh dùng seed riêng (`42 + id`), nên nhân vật có thể trông khác nhau giữa các cảnh |
| Clip `.mp4` từ bên ngoài (Google Flow...) | ❌ Chưa thử với file thật |

**Lưu ý về video mẫu bạn gửi** (`Download.mp4`, `Download__1_.mp4`): cả hai đều 576x1024, 30fps. Builder sẽ phóng lên 1080x1920 nên hình sẽ mềm. Nếu dùng làm cảnh động, nên xuất lại ở độ phân giải cao hơn.

---

## 10. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| `thiếu 00N.png ... trong assets` | Giải nén `scenes.zip` đúng chỗ, tên file phải là 3 chữ số |
| `ffmpeg lỗi ... ass` | FFmpeg không có libass. Cài bản đầy đủ, hoặc thêm `--no-subs` để thử |
| Giọng đọc `No audio was received` | Edge-TTS từ chối. Builder thử lại 4 lần; nếu vẫn lỗi, chờ vài phút hoặc đặt file giọng vào `assets/audio/` |
| Kaggle `CUDA out of memory` | Chạy lại notebook (đã có cơ chế bỏ qua ảnh đã vẽ). Có thể giảm `WIDTH/HEIGHT` xuống 640x1152 |
| Prompt bị cắt (`> 77 token`) | Rút gọn `image_prompt`, bỏ tính từ thừa |
| Phụ đề chữ bị lệch dòng dài | Đổi `KARAOKE_WORDS` (mặc định 5) trong `story.py` |
| File quá 25 MB | Tăng CRF lên 26, hoặc giảm bitrate AAC xuống 96k |

---

## 11. Lộ trình tiếp theo (chưa làm)

- Giữ nhân vật nhất quán giữa các cảnh: dùng cùng seed cho nhân vật, hoặc một ảnh tham chiếu (IP-Adapter).
- Notebook Kaggle đầy đủ: dán kịch bản → sinh storyboard qua Gemini API → vẽ → đóng gói.
- Tích hợp vào `DubStudio` làm tab Story Studio, dùng chung cache và TTS adapter.
- Thêm engine Azure Speech (có word boundary cho karaoke chính xác hơn).

---

## 12. Storyboard v2: giọng ElevenLabs có mốc thời gian

Dùng khi muốn thời lượng cảnh và phụ đề karaoke lấy từ **mốc thật của giọng đọc**, không ước lượng.

### Luồng

```bash
python -m story.v2 validate projects/ep02/storyboard_v2.json   # kiểm tra schema
python -m story.v2 script projects/ep02                        # ghi full_script.txt
# gọi ElevenLabs /with-timestamps MỘT lần, có cache, rồi dựng:
export ELEVENLABS_API_KEY=...
python -m story.v2 build projects/ep02 --voice-id <VOICE_ID>
# hoặc dùng mp3 và alignment.json đã có:
python -m story.v2 build projects/ep02 --audio a.mp3 --alignment a.alignment.json
```

### Quy tắc quan trọng

- **Văn bản gửi đi là bản ghép từ các cảnh** (`full_script.txt`), không phải trường `full_script_for_elevenlabs`. Trường đó chỉ để tham chiếu; nếu khác bản ghép, hệ thống cảnh báo.
- **Số từ phải khớp.** Nếu alignment trả về số từ khác kịch bản đã gửi, hệ thống dừng trước khi render và báo lỗi. Thường do sửa văn bản sau khi gọi API. Khi đó chạy lại `script` rồi `build`.
- **`tts_text` (nếu có) phải có cùng số từ với `speech`.** Phụ đề hiển thị theo `speech`, mốc lấy từ `tts_text`, nên hai bên phải tương ứng từng từ.
- **Cache:** kết quả được lưu theo SHA-256 của (văn bản, giọng, model). Chạy lại cùng văn bản không tốn thêm credit.
- **Clip Flow dài 8 giây:** nếu cảnh dài hơn, hệ thống cảnh báo và lặp clip. Nên tách cảnh.
- **`ken_burns` là trường chuyển động**, không phải `motion`. `sfx` hợp lệ gồm `none, whoosh, boom, pop, typing, alarm, ship_horn`.

### Kiểm chứng

| Hạng mục | Trạng thái |
|---|---|
| Schema v2 (lỗi cứng, cảnh báo, tts_text khác số từ) | ✅ Test tự động; JSON mẫu của bạn chạy đúng |
| Ánh xạ mốc sang cảnh, nối khít, giọng nằm trọn trong khung | ✅ Test tự động (có đột biến bắt được lỗi) |
| Kiểm tra số từ khớp giữa gửi đi và alignment | ✅ Test tự động (có đột biến bắt được lỗi) |
| Cache ElevenLabs, thử lại khi 429, dừng ngay khi 401 | ✅ Test với HTTP giả lập |
| Phụ đề karaoke theo mốc từng từ, đổi thời gian ASS | ✅ Test tự động |
| Dựng MP4 end-to-end với alignment | ✅ Test tự động với aligner giả lập |
| **Gọi ElevenLabs thật** | ❌ Chưa chạy (môi trường không có mạng và API key) |
| **Clip Flow thật của bạn** | ❌ Chưa kiểm tra. File mẫu bạn gửi là 576x1024, không phải 720x1280 |
| Nội dung lịch sử trong JSON mẫu | ❌ Chưa đối chiếu nguồn. Đây là dữ liệu bạn cung cấp |
