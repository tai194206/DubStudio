# 🚀 DubStudio V5.2 Quickstart (5 Bước Thực Chiến 9:16)

Hướng dẫn ngắn quy trình sản xuất 1 tập video lịch sử dọc `1080x1920` chuẩn **Pipeline V5.2** (ưu tiên tư liệu thật + Google Flow + DubStudio v3.2).

---

## Bước 1: Chuẩn bị kịch bản nguồn (`.txt` / `series_plan.md`)
- Lưu tư liệu sự kiện / kịch bản thô dạng `.txt` (hoặc cập nhật `series_plan.md` theo mạch tập trước).
- Chia mỗi tập dài vừa vặn **45–60 giây**, mở đầu bằng **Hook 3 giây** (câu hỏi / con số gây tò mò ngay cảnh 1) và kết bằng cliffhanger kéo bình luận.

## Bước 2: Tạo & kiểm định kịch bản phân cảnh (`v5_produce`)
- Chạy workflow `.agent/workflows/v5_produce.md` theo luật **V5.2**:
  - Ưu tiên hạng **`0_100`** (85–100% ảnh tư liệu thật; nếu thiếu mới hạ `30_70` / `50_50` / `70_30`).
  - Xuất ra `storyboard.json` + `archive_checklist.md`.
- Kiểm định & đồng bộ trước khi chạy sản xuất:
  ```bash
  python automation/scripts/validate_storyboard.py <episode_dir>/storyboard.json --sync-files
  ```

## Bước 3: Chuẩn bị giọng đọc (`assets/full_audio.mp3`)
- Thu/xuất giọng đọc liền mạch toàn tập (ElevenLabs hoặc Edge-TTS) và lưu tại:
  `<episode_dir>/assets/full_audio.mp3`
- Khi dựng phim, `StoryPipeline` tự dùng `faster-whisper` căn mốc từng từ trên bản gốc `1.00x` và lưu cache `cache/align_*.json` (sai số `0.01s`, đổi tốc độ/phụ đề không phải nhận diện lại).

## Bước 4: Săn ảnh & video tư liệu thật rồi chạy Google Flow (`v5_run_all`)
- Dùng `automation/scripts/fetch_archive_media.py` (theo từ khóa Việt / Anh / Pháp trong `archive_checklist.md`) để săn ứng viên ảnh (**Wikimedia Commons**) và clip video (**Internet Archive** kèm lưới xem trước 6 khung hình `_contact.jpg`) vào `automation/debug/archive_candidates/<ten_tap>/`.
- Duyệt trực quan đảm bảo **đúng thời kỳ, 0 logo/watermark (`VTV/INA/AP/Reuters/Getty/British Pathé`)** rồi chuẩn hóa nạp vào `assets/` bằng `--mode commit-image` (`{id:03d}.jpg`) hoặc `--mode commit-video` (`{id:03d}.mp4`, đã lột tiếng `-an`, tự ghi `archive_sources.json`).
- Sau đó chạy workflow `.agent/workflows/v5_run_all.md` (`automation/scripts/run_episode.py` + `flow_bot.py` batch pipelining): tự bỏ qua các cảnh đã có tư liệu thật (`SKIP_EXISTING_ASSET`) và chỉ gen bù các cảnh thiếu (`0 tín dụng`).

## Bước 5: Xuất video 9:16 trong DubStudio GUI
1. Mở giao diện:
   ```bash
   python run.py
   ```
2. Vào tab **Story Studio 9:16 → `1. Dự án & Giọng`**:
   - Chọn `storyboard.json` (tự điền `assets/` và tự nhận `full_audio.mp3` nếu có).
   - Chọn **Nhạc nền (BGM)** (hỗ trợ cả `.mp3/.wav` lẫn `.mp4` — giữ nguyên tốc độ gốc `1.00x`).
   - Chỉnh **Tốc độ đầu ra** (tự đồng bộ Video + Giọng + Phụ đề) và thanh **Âm lượng SFX** (`0%`–`150%`, mặc định `85%`, kéo `0%` để tắt).
3. Sang tab **`2. Phụ đề & Kênh`**: chọn kiểu **`Cụm 3 chữ sáng từng từ (CapCut / highlight_3words)`**.
4. Sang tab **`3. Dựng & Hậu kỳ`**: bật **Né quét AI (`Nhẹ / Vừa / Mạnh`)** (áp 1-pass trước lớp chữ `.ass` giúp chữ luôn thẳng sắc nét) rồi bấm xuất MP4. *(Hoặc dùng tab **`4. Né quét AI`** để xử lý riêng file `.mp4` đã xuất sẵn.)*
