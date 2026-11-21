---
description: Quy trình tự động chạy trọn gói V5.2 (Săn ảnh tư liệu thật trước -> Gen bù 0đ bằng Flow -> Video nếu có -> Tự cứu lỗi Policy -> Kiểm định DubStudio)
---

# WORKFLOW CHẠY TRỌN GÓI 1 TẬP V5.2 (`v5_run_all`)

Khi người dùng yêu cầu *"chạy tự động Tập X"* hoặc *"gen nốt Tập X"* cho một thư mục tập phim `<episode_dir>` trong `story_mode/projects/<Ten_Series>/<Ten_Tap>/`, thực hiện đúng trình tự sau:

## Bước 1 — Săn & duyệt ảnh tư liệu lịch sử thật (Ưu tiên hàng đầu với hạng `0_100` / `30_70`)
- Trước khi gọi Google Flow tạo ảnh bù, kiểm tra nếu có `automation/scripts/fetch_archive_images.py` (hoặc dựa theo `<episode_dir>/archive_checklist.md`) để săn và lưu ảnh tư liệu thật (Wikimedia Commons / ảnh lịch sử gốc) vào đúng tên file `<episode_dir>/assets/{id:03d}.jpg` (crop/chuẩn hóa sạch `9:16` hoặc ảnh sắc nét).
- Duyệt nhanh chất lượng các ảnh vừa săn: xóa hoặc bỏ qua những tấm không đúng sự kiện/bị chữ đè nặng để Bước 3 tự gen ảnh phong cách phim tư liệu dự phòng (`0 tín dụng`).

## Bước 2 — Kiểm tra hiện trạng & bảo vệ thành phẩm có sẵn (`0 tín dụng`)
Chạy lệnh kiểm tra tổng thể (không cần mở Chrome):
```powershell
.venv\Scripts\python automation/scripts/run_episode.py "<episode_dir>" --phase check
```
- Xác nhận `storyboard.json` PASS 100%, kiểm tra danh sách ảnh thật đã thả sẵn trong `assets/` (sẽ tự động kích hoạt `SKIP_EXISTING_ASSET`, không gen đè), các cảnh còn thiếu và cảnh báo `[NOTICE]` (nếu có).

## Bước 3 — Gen bù ảnh gốc còn thiếu trên Google Flow (`0 tín dụng`, đồng loạt 4 ảnh/đợt)
Mở Chrome CDP (`automation\scripts\launch_chrome_cdp.bat`) vào đúng dự án Google Flow, rồi chạy:
```powershell
.venv\Scripts\python automation/scripts/run_episode.py "<episode_dir>" --phase images
```
- Bot tự động **bỏ qua toàn bộ cảnh đã có ảnh thật trong `assets/{id:03d}.jpg`**, chỉ gen bù những cảnh còn thiếu bằng `Nano Banana Pro (9:16, x1, 0 tín dụng)`, mặc định **4 ảnh/đợt** (`--concurrency 4`), và tự xuất `assets/{id:03d}.jpg` cho `static_image`.

## Bước 4 — Tự cứu lỗi Policy ảnh (nếu có)
Đọc `<episode_dir>/pipeline_state.json`:
- Nếu có bất kỳ cảnh nào mang `"image_status": "policy_error"`:
  1. Thực hiện đúng quy trình trong `.agent/workflows/v5_heal_policy.md` để viết mềm `image_prompt`.
  2. Chạy `validate_storyboard.py` đến khi PASS 100% (0 ERROR) và đặt lại trạng thái các cảnh vừa sửa về `"pending"`.
  3. Chạy bù riêng các cảnh lỗi đó:
     ```powershell
     .venv\Scripts\python automation/scripts/run_episode.py "<episode_dir>" --phase images --scenes <id1,id2,...>
     ```

## Bước 5 — Chạy Video Veo 3.1 (CHỈ chạy nếu tập có cảnh `flow_video > 0`)
- **Nhánh tối ưu hạng `0_100` thuần tư liệu (0 cảnh `flow_video`):** **BỎ QUA hoàn toàn bước này** (`0 tín dụng`), chuyển thẳng sang Bước 6!
- **Nếu tập có cảnh `flow_video` chưa tạo xong:**
  - Nếu người dùng **chưa** xác nhận tiêu thụ tín dụng: DỪNG LẠI báo cáo kết quả ảnh và **tổng tín dụng Veo 3.1 dự kiến cần dùng** để người dùng duyệt.
  - Nếu người dùng **đã đồng ý** chạy trọn gói: chạy đồng loạt mặc định **3 video/đợt**, chia mẻ 5 cảnh:
    ```powershell
    .venv\Scripts\python automation/scripts/run_episode.py "<episode_dir>" --phase videos --batch-size 5 --yes
    ```
  - Nếu có cảnh mang `"video_status": "policy_error"` trong `pipeline_state.json`: áp dụng `.agent/workflows/v5_heal_policy.md` viết mềm `motion_prompt`, validate PASS, reset cảnh lỗi về `"pending"`, rồi chạy bù `--scenes <id1,id2,...> --yes`.

## Bước 6 — Nghiệm thu cuối & Bàn giao sang DubStudio
Chạy lại kiểm tra tổng thể:
```powershell
.venv\Scripts\python automation/scripts/run_episode.py "<episode_dir>" --phase check
```
- Báo cáo kết quả hoàn tất cho người dùng (`READY FOR DUBSTUDIO` hoặc danh sách còn thiếu).
- Nếu chưa có `assets/full_audio.mp3`, nhắc người dùng lấy `<episode_dir>/elevenlabs.txt` tạo voice đặt vào `assets/full_audio.mp3` rồi mở **DubStudio → Story Studio 9:16** (tốc độ `1.15x–1.20x`, phụ đề `highlight_3words`, Né quét AI `Vừa` + BGM `.mp3/.mp4` giữ nguyên `1.00x`) để xuất video.
