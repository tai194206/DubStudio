# Hướng dẫn bỏ túi — Pipeline Sản xuất Series Lịch sử V5

## 1. Quy trình 4 bước từ `.txt` → Video DubStudio 9:16
1. **Nạp kiến thức**: Đặt file sử liệu vào `automation/inputs/<ten_su_kien>.txt`.
2. **Sinh kịch bản & JSON**: Gọi Agent chạy workflow `v5_produce` (xuất `series_plan.md`, `Caption.md`, `storyboard.json`, `elevenlabs.txt`, `timeline_script.txt` và tự kiểm định PASS 100%).
3. **Tạo giọng & tài nguyên hình/video**:
   - Đem `elevenlabs.txt` xuất giọng đọc lưu vào `assets/full_audio.mp3`.
   - Mở Chrome CDP vào Google Flow và chạy `run_episode.py` để gen ảnh (`0 tín dụng`) + video Veo 3.1 - Lite (`10 tín dụng/clip`, tự lột tiếng gốc bằng FFmpeg).
4. **Dựng phim**: Mở DubStudio (`python run.py`) → tab **Story Mode** → chọn `storyboard.json` & thư mục `assets/` → bấm **Xuất video 9:16**.

---

## 2. Mở Chrome CDP kết nối Google Flow
Chạy trực tiếp file batch (cổng debug `9222`, lưu đăng nhập riêng ở `automation/chrome_profile/`):
```powershell
automation\scripts\launch_chrome_cdp.bat
```
- Lần đầu: đăng nhập Google Flow (`flow.google.com`), mở sẵn đúng **Dự án / Bộ sưu tập Tập** cần chạy.
- **Giữ nguyên cửa sổ Chrome này** trong suốt quá trình bot làm việc.

---

## 3. 4 lệnh `run_episode.py` hay dùng nhất
Thay `<EP>` bằng đường dẫn thư mục tập (vd: `story_mode/projects/Dien_Bien_Phu_1954/Dien_Bien_Phu_Tap_02`):

```powershell
# 1. Kiểm tra hiện trạng, thành phẩm có sẵn, cảnh thiếu & ước tính tín dụng (0 tín dụng)
.venv\Scripts\python automation/scripts/run_episode.py "<EP>" --phase check

# 2. Gen toàn bộ ảnh gốc còn thiếu (0 tín dụng; tự bảo vệ cảnh đã có trong assets/)
.venv\Scripts\python automation/scripts/run_episode.py "<EP>" --phase images

# 3. Gen video cho các cảnh flow_video còn thiếu (tự chia mẻ 5 cảnh/lần, 10 tín dụng/clip)
.venv\Scripts\python automation/scripts/run_episode.py "<EP>" --phase videos --batch-size 5

# 4. Chạy trọn gói 1 chạm (gen ảnh -> tạm dừng cho duyệt raw_images/ -> chạy video)
.venv\Scripts\python automation/scripts/run_episode.py "<EP>" --phase all --batch-size 5
```
> Mẹo: Thêm `--dry-run` vào bất kỳ lệnh nào để xem trước kế hoạch (`0 tín dụng`, không mở Chrome).

---

## 4. Xử lý nhanh ảnh xấu & lỗi Policy
- **Gen lại 1 ảnh xấu (`0 tín dụng`)**: Xóa file `raw_images/{id:03d}.png` (và `assets/{id:03d}.jpg` nếu là `static_image`), rồi chạy lại `--phase images` — bot chỉ gen lại đúng ảnh vừa xóa.
- **Gặp lỗi Google Flow Policy (`policy_error`)**: Không chạy lại mù. Nhắn Agent chạy workflow **`v5_heal_policy`** cho tập phim đó — Agent sẽ đọc `pipeline_state.json`, viết mềm prompt điện ảnh an toàn, kiểm định PASS 100%, reset trạng thái và in sẵn lệnh chạy bù `--scenes ...`.
