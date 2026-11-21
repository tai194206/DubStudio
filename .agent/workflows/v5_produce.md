---
description: Quy trình tự động sản xuất Series Lịch sử TikTok V5.2 (Ưu tiên 100% Tư liệu thật + Hook 3s + Giữ chân TikTok) từ file kiến thức .txt ra đủ file cho DubStudio v3.2
---

# WORKFLOW SẢN XUẤT SERIES V5.2

Khi người dùng chỉ định 1 file `.txt` trong `automation/inputs/` hoặc yêu cầu sản xuất 1 tập cụ thể, thực hiện đúng trình tự sau:

## Bước 1 — Giai đoạn 0 (Đánh giá Tư liệu, Lập bản đồ Series & Caption)
1. Đọc toàn bộ file `.txt` đầu vào trong `automation/inputs/`.
2. **Đánh giá độ phong phú kho ảnh tư liệu thật** của sự kiện và chốt rõ **Hạng tỉ lệ (`0_100` / `30_70` / `50_50` / `70_30`)** + lý do chọn theo luật V5.2 trong `.agent/rules/dubstudio_v5_rules.md`.
   - **Ưu tiên mặc định hạng `0_100`** (`0%–15% flow_video` / `85%–100% static_image`, có thể dùng `0` clip AI) cho các series cận đại – hiện đại giàu kho ảnh tư liệu như Điện Biên Phủ 1954 (từ Tập 4 trở đi), Kháng chiến chống Mỹ...
3. Chia số tập linh hoạt để phủ 100% kiến thức (mặc định mỗi tập **20–22 cảnh**, ~300–350 từ, ~80–100s ở `1.20x` để tối đa Completion Rate; trừ sự kiện quá lớn mới dùng 24–26 cảnh).
4. Tạo thư mục `story_mode/projects/<Ten_Series>/` và xuất 2 file:
   - `series_plan.md`: Ghi rõ mục **Đánh giá kho tư liệu thật & Hạng tỉ lệ (`0_100` / `30_70` / `50_50` / `70_30`) + lý do chọn**, kèm dàn ý đầy đủ từng tập (Style A/B/C, Hook 3s Cảnh 1, nối mạch tại Cảnh 2 nếu cần, Re-hook giữa tập, 2–3 cảnh bản đồ chiến thuật, Cliffhanger + CTA mồi bình luận).
   - `Caption.md`: Bộ caption TikTok đầy đủ cho cả series đúng chuẩn hashtag trong rules.
5. Hiển thị bảng tổng quan các tập + Hạng tỉ lệ đề xuất để người dùng chốt trước khi chạy Bước 2 (trừ khi người dùng yêu cầu làm ngay Tập X).

## Bước 2 — Sản xuất trọn gói 1 Tập (Giai đoạn 1 + 2 + 3 nội bộ)
Làm dứt điểm từng tập một để không bị viết lướt:
1. Tạo đủ thư mục:
   - `story_mode/projects/<Ten_Series>/<Ten_Tap>/assets/`
   - `story_mode/projects/<Ten_Series>/<Ten_Tap>/raw_images/`
2. Khóa chuẩn V5.2 & **Zero-Fabrication** cho cả tập:
   - **Kiểm soát sự thật lịch sử tuyệt đối (Zero-Fabrication):** Không bịa ngày tháng, quân số, tên đơn vị, thương vong, địa danh hay câu nói của nhân vật; mọi chi tiết phải bám `.txt` đầu vào hoặc chính sử công nhận rộng rãi (không chắc 100% thì bỏ).
   - **Cảnh 1 (Hook 3s đầu):** Ngắn **8–13 từ**, mở thẳng bằng câu hỏi *"Tại sao...?"* / nghịch lý / con số sốc / cú bẻ lái đắt nhất tập (CẤM *"Tập trước..."*, *"Hôm nay..."*, *"Chào mừng..."*), kèm `sfx` mạnh tại `0.00s` (`boom` / `whoosh` / `alarm`) và `zoom_in` dứt khoát (nếu là ảnh tĩnh). Nếu cần nhắc tập trước thì gói gọn 1 câu vào **Cảnh 2**.
   - **Cú bẻ nhịp giữa tập (Cảnh 9–11):** Đặt bước ngoặt bất ngờ kèm `sfx` chuyển hồi rõ rệt.
   - **2–3 cảnh Bản đồ chiến thuật:** Đặt đúng lúc phân tích thế trận, dùng prompt bản đồ quân sự độ tương phản cao (ưu tiên hàng đầu khi thiếu ảnh thật).
   - **Cảnh cuối (Cliffhanger + CTA):** Dẫn sang tập sau + chèn câu hỏi mở / lời tri ân kích thích bình luận.
   - **Sound Design (`sfx`):** Chọn lọc **5–8 điểm `sfx` đắt giá** trong tập (dùng đúng bộ tag hợp lệ `VALID_SFX`).
   - **Hình ảnh & `ken_burns`:** Đổi chiều `ken_burns` liên tục giữa các cảnh `static_image` liền kề (không dùng `"static"`); cảnh không có ảnh thật ưu tiên bản đồ chiến thuật / địa hình / núi rừng biểu tượng, **KHÔNG** dùng AI tạo giả ảnh nhân vật thật làm điều không có trong lịch sử; `image_prompt` dự phòng viết theo phong cách ảnh phim tư liệu thật (`authentic archival documentary photograph, black-and-white or subtle aged sepia, candid wartime photojournalism, natural 35mm film grain, historical realism`) — CẤM 3D/AI bóng loáng.
3. Tạo đủ **6 file** trong `story_mode/projects/<Ten_Series>/<Ten_Tap>/`:
   - `timeline_script.txt`: Kịch bản kèm mốc `[HH:MM:SS.mmm -> HH:MM:SS.mmm]`.
   - `elevenlabs.txt`: Lời đọc sạch từng dòng để người dùng tạo `assets/full_audio.mp3`.
   - `fact_check.md`: (**BẮT BUỘC**) Bảng kiểm chứng mọi mốc thời gian, quân số, tên đơn vị, thương vong, nhân vật, địa danh xuất hiện trong kịch bản kèm nguồn đối chiếu.
   - `storyboard.json`: Khai báo `"asset_ratio_tier": "0_100" | "30_70" | "50_50" | "70_30"` ở root, đầy đủ `locations`, `beats`, `scenes` (`motion_prompt` viết cho `flow_video`, để `""` cho `static_image`).
   - `archive_checklist.md`: Bảng danh sách riêng toàn bộ cảnh cần tư liệu gồm `ID`, tên file đích `assets/{id:03d}.jpg` / `.mp4`, mô tả nội dung tư liệu cần tìm, và từ khóa tìm kiếm (tiếng Việt + tiếng Anh + tiếng Pháp).
   - `pipeline_state.json`: Khởi tạo danh sách trạng thái từng cảnh (`image_status: "pending"`, `video_status: "pending"` với `flow_video` hoặc `"skipped"` với `static_image`).

## Bước 3 — Tự kiểm định bằng `validate_storyboard.py` (Self-Healing)
1. Chạy lệnh kiểm định & đồng bộ:
   `.venv\Scripts\python automation/scripts/validate_storyboard.py "story_mode/projects/<Ten_Series>/<Ten_Tap>/storyboard.json" --sync-files`
2. Nếu có bất kỳ lỗi ERROR hoặc WARNING V5.2 nào (schema, Whisper sync, từ cấm Flow, Hook 3s > 14 từ / mở đầu cấm / thiếu SFX, trùng `ken_burns`, lệch biên tỉ lệ hạng...), TỰ ĐỘNG sửa `storyboard.json` (và cập nhật `archive_checklist.md` + `fact_check.md` nếu đổi nội dung) rồi chạy lại `--sync-files` đến khi PASS 100% (`0 ERROR`).
3. Báo cáo ngắn gọn: đường dẫn thư mục tập, Hạng tỉ lệ (`0_100` / `30_70` / `50_50` / `70_30`), tổng số cảnh (`flow_video` vs `static_image`), ước tính tín dụng Veo 3.1 (`0` nếu thuần `0_100`), thời lượng `1.00x` & `1.20x`, và nhắc người dùng:
   - Lấy `elevenlabs.txt` xuất `assets/full_audio.mp3`.
   - Chạy `automation/scripts/fetch_archive_media.py` (săn ảnh Wikimedia Commons + video Internet Archive theo `archive_checklist.md`, duyệt contact sheet loại watermark/sai thời kỳ rồi nạp vào `assets/`) **trước khi chạy `flow_bot.py`** để tận dụng `SKIP_EXISTING_ASSET` và chỉ gen bù các cảnh thiếu (`0 tín dụng`).
