---
description: Quy trình tự động sản xuất Series Lịch sử Dual-Delivery V5.4 (TikTok 9:16 + YouTube 16:9 song song `assets_16x9/` + Tư liệu thật + Chống bóng nhựa AI) từ file kiến thức .txt ra đủ file cho DubStudio
---

# WORKFLOW SẢN XUẤT SERIES DUAL-DELIVERY V5.4

Khi người dùng chỉ định 1 file `.txt` trong `automation/inputs/` hoặc yêu cầu sản xuất 1 tập cụ thể, thực hiện đúng trình tự sau:

## Bước 1 — Giai đoạn 0 (Đánh giá Tư liệu, Khóa `frame_preset` & Lập bản đồ Series)
1. Đọc toàn bộ file `.txt` đầu vào trong `automation/inputs/`.
2. **Đánh giá độ phong phú kho ảnh tư liệu thật & phân nhánh thời kỳ** theo luật V5.3 trong `.agent/rules/dubstudio_v5_rules.md`:
   - **Sử cận/hiện đại (`1945–1979`):** Ưu tiên mặc định Hạng `0_100` (`0%–15% flow_video` / `85%–100% static_image`, có thể dùng `0` clip AI), săn tư liệu bằng `--exclude-modern`.
   - **Sử cổ/trung đại:** Chọn nhánh mỹ thuật **tranh tư liệu bảo tàng / tranh sơn dầu cổ điển / tranh mực tàu giấy dó** xen kẽ bản đồ cổ & di tích/hiện vật thật (`--include-sites`).
3. **Chốt cứng 1 `frame_preset` duy nhất cho toàn series** (`framed_4_5`, `framed_1_1`, `portrait_9_16`, hoặc `landscape_16_9`) và ghi rõ vào `series_plan.md` để đồng bộ mọi tập.
4. Chia số tập linh hoạt để phủ 100% kiến thức (mặc định mỗi tập **20–22 cảnh**, ~300–350 từ, ~80–100s ở `1.20x` để tối đa Completion Rate; trừ sự kiện quá lớn mới dùng 24–26 cảnh).
5. Tạo thư mục `story_mode/projects/<Ten_Series>/` và xuất 2 file:
   - `series_plan.md`: Ghi rõ **`frame_preset` cố định của series**, **nhánh phong cách thời kỳ**, **Đánh giá kho tư liệu thật & Hạng tỉ lệ (`0_100` / `30_70` / `50_50` / `70_30`) + lý do chọn**, kèm dàn ý đầy đủ từng tập (Style A/B/C, Hook 3s Cảnh 1, nối mạch tại Cảnh 2 nếu cần, Re-hook giữa tập, 2–3 cảnh bản đồ chiến thuật, Cliffhanger + CTA mồi bình luận).
   - `Caption.md`: Bộ caption TikTok đầy đủ cho cả series đúng chuẩn hashtag trong rules.
6. Hiển thị bảng tổng quan các tập + `frame_preset` + Hạng tỉ lệ đề xuất để người dùng chốt trước khi chạy Bước 2 (trừ khi người dùng yêu cầu làm ngay Tập X).

## Bước 2 — Sản xuất trọn gói 1 Tập (Giai đoạn 1 + 2 + 3 nội bộ)
Làm dứt điểm từng tập một để không bị viết lướt:
1. Tạo đủ thư mục:
   - `story_mode/projects/<Ten_Series>/<Ten_Tap>/assets/`
   - `story_mode/projects/<Ten_Series>/<Ten_Tap>/raw_images/`
2. Khóa chuẩn V5.3 & **Zero-Fabrication** cho cả tập:
   - **Kiểm soát sự thật lịch sử tuyệt đối (Zero-Fabrication):** Không bịa ngày tháng, quân số, tên đơn vị, thương vong, địa danh hay câu nói của nhân vật; mọi chi tiết phải bám `.txt` đầu vào hoặc chính sử công nhận rộng rãi (không chắc 100% thì bỏ).
   - **Cảnh 1 (Hook 3s đầu):** Ngắn **8–13 từ**, mở thẳng bằng câu hỏi *"Tại sao...?"* / nghịch lý / con số sốc / cú bẻ lái đắt nhất tập (CẤM *"Tập trước..."*, *"Hôm nay..."*, *"Chào mừng..."*), kèm `sfx` mạnh tại `0.00s` (`boom` / `whoosh` / `alarm`) và `zoom_in` dứt khoát (nếu là ảnh tĩnh). Nếu cần nhắc tập trước thì gói gọn 1 câu vào **Cảnh 2**.
   - **Cú bẻ nhịp giữa tập (Cảnh 9–11):** Đặt bước ngoặt bất ngờ kèm `sfx` chuyển hồi rõ rệt.
   - **2–3 cảnh Bản đồ chiến thuật:** Đặt đúng lúc phân tích thế trận, dùng prompt bản đồ quân sự độ tương phản cao (ưu tiên hàng đầu khi thiếu ảnh thật).
   - **Cảnh cuối (Cliffhanger + CTA):** Dẫn sang tập sau + chèn câu hỏi mở / lời tri ân kích thích bình luận.
   - **Sound Design (`sfx`):** Chọn lọc **5–8 điểm `sfx` đắt giá** trong tập (dùng đúng bộ tag hợp lệ `VALID_SFX`).
   - **Hình ảnh, Khung hình & Chống bóng nhựa AI:** Đổi chiều `ken_burns` liên tục giữa các cảnh `static_image` liền kề (không dùng `"static"`); ghi đồng bộ `"frame_preset"` của series vào `storyboard.json` và khóa đúng cú pháp framing tương ứng trong `image_prompt`. Hạn chế tối đa vẽ cận mặt người thật bằng AI (ưu tiên góc rộng, sau vai, bóng ngược sáng, bản đồ, hiện vật, địa hình); CẤM `8k, hyper-realistic, dramatic rim lighting, glossy, cinematic HDR, orange and teal`, thay bằng `matte archival film grain, natural flat daylight` (hoặc tranh tư liệu/sơn dầu/mực tàu cho sử cổ đại) và khóa đúng trang phục/khí tài lịch sử Việt Nam.
3. Tạo đủ **6 file** trong `story_mode/projects/<Ten_Series>/<Ten_Tap>/`:
   - `timeline_script.txt`: Kịch bản kèm mốc `[HH:MM:SS.mmm -> HH:MM:SS.mmm]`.
   - `elevenlabs.txt`: Lời đọc sạch từng dòng để người dùng tạo `assets/full_audio.mp3`.
   - `fact_check.md`: (**BẮT BUỘC**) Bảng kiểm chứng mọi mốc thời gian, quân số, tên đơn vị, thương vong, nhân vật, địa danh xuất hiện trong kịch bản kèm nguồn đối chiếu.
   - `storyboard.json`: Khai báo `"frame_preset"` và `"asset_ratio_tier": "0_100" | "30_70" | "50_50" | "70_30"` ở root, đầy đủ `locations`, `beats`, `scenes` (`motion_prompt` viết cho `flow_video`, để `""` cho `static_image`).
   - `archive_checklist.md`: Bảng danh sách riêng toàn bộ cảnh cần tư liệu gồm `ID`, tên file đích `assets/{id:03d}.jpg` / `.mp4`, mô tả nội dung tư liệu cần tìm, và từ khóa tìm kiếm (tiếng Việt + tiếng Anh + tiếng Pháp).
   - `pipeline_state.json`: Khởi tạo danh sách trạng thái từng cảnh (`image_status: "pending"`, `video_status: "pending"` với `flow_video` hoặc `"skipped"` với `static_image`).

## Bước 3 — Tự kiểm định bằng `validate_storyboard.py` (Self-Healing)
1. Chạy lệnh kiểm định & đồng bộ:
   `.venv\Scripts\python automation/scripts/validate_storyboard.py "story_mode/projects/<Ten_Series>/<Ten_Tap>/storyboard.json" --sync-files`
2. Nếu có bất kỳ lỗi ERROR hoặc WARNING V5.3 nào (schema, Whisper sync, từ cấm Flow, thiếu cue `frame_preset`, Hook 3s > 14 từ / mở đầu cấm / thiếu SFX, trùng `ken_burns`, lệch biên tỉ lệ hạng...), TỰ ĐỘNG sửa `storyboard.json` (và cập nhật `archive_checklist.md` + `fact_check.md` nếu đổi nội dung) rồi chạy lại `--sync-files` đến khi PASS 100% (`0 ERROR`).
3. Báo cáo ngắn gọn: đường dẫn thư mục tập, `frame_preset` + Hạng tỉ lệ (`0_100` / `30_70` / `50_50` / `70_30`), tổng số cảnh (`flow_video` vs `static_image`), ước tính tín dụng Veo 3.1 (`0` nếu thuần `0_100`), thời lượng `1.00x` & `1.20x`, và nhắc người dùng:
   - *(Khuyến nghị V5.4 cho YouTube Series)*: có thể gắn thêm `"chapter_title"` (cấp root JSON) và `"is_cta": true` ở cảnh cuối mời xem tập sau (hỗ trợ kiểm tra sạch qua `validate_storyboard.py`).
   - Lấy `elevenlabs.txt` xuất `assets/full_audio.mp3`.
   - Chạy `automation/scripts/fetch_archive_media.py` (mặc định `--exclude-modern` cho sử cận/hiện đại, hoặc thêm `--include-sites` cho sử cổ đại/di tích; duyệt contact sheet loại watermark/sai thời kỳ rồi nạp vào `assets/`) **trước khi chạy `flow_bot.py`** để tận dụng `SKIP_EXISTING_ASSET` và chỉ gen bù các cảnh thiếu theo đúng `frame_preset` (`0 tín dụng`).

## Bước 4: Nâng cấp & Sản xuất Song song Bản YouTube 16:9 Nguyên bản (`assets_16x9/`) — V5.4 Dual-Delivery
**NGUYÊN TẮC AN TOÀN:** Tuyệt đối KHÔNG sửa hay ghi đè `storyboard.json`, `assets/full_audio.mp3`, `assets/` hay `raw_images/` của bản TikTok/Shorts. Chỉ xuất song song vào `raw_images_16x9/` và `assets_16x9/`!

1. **Nạp ảnh tư liệu thật bản ngang `1920x1080` vào `assets_16x9/`** (tùy chọn — để các tập lịch sử dùng ảnh thật full-bleed không viền mờ):
   ```powershell
   .venv\Scripts\python automation\scripts\fetch_archive_media.py --mode commit-image --episode-dir story_mode\projects\<Ten_Series>\<Tap_XX> --scene <id> --input <anh_goc.jpg> --youtube-16x9
   ```
   *(Tự động crop/scale về `1920x1080` trong `assets_16x9/`, đồng thời ghi bổ sung `asset_file_16x9` trong `archive_sources.json` mà giữ nguyên thông tin `assets/` cũ).*

2. **Chạy Google Flow sinh bù toàn bộ cảnh còn thiếu ở tỉ lệ `16:9` gốc (`assets_16x9/`):**
   ```powershell
   .venv\Scripts\python automation\scripts\run_episode.py story_mode\projects\<Ten_Series>\<Tap_XX> --phase all --youtube-16x9
   ```
   *(Hoặc chạy từng pha với `flow_bot.py <Tap_XX>\storyboard.json --youtube-16x9 --mode images` rồi `--mode videos` — tự động giữ ảnh tư liệu đã có trong `assets_16x9/` nhờ `SKIP_EXISTING_ASSET`, chuyển tạm cụm chỉ thị khung dọc trong prompt sang bố cục ngang `16:9 widescreen`, xuất ảnh/clip vào `raw_images_16x9/` và `assets_16x9/`).*

3. **Biên tập gộp toàn Series trong Tab `📺 YouTube 16:9`:**
   - Chọn thư mục series và bật `[x] Ưu tiên tài nguyên assets_16x9/ (tự lùi về assets/ nếu thiếu)` → `core/series_compiler.py` tự động ưu tiên `.mp4`/`.jpg` ngang từ `assets_16x9/`, dùng lại `assets/full_audio.mp3`, lùi về `assets/` nền mờ cho cảnh chưa có 16:9, và ghi thống kê vào `compliance_report.json`.
