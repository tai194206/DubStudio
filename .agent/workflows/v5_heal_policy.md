---
description: Quy trình tự sửa các cảnh bị Google Flow chặn Policy (policy_error) trong Pipeline Lịch sử V5
---

# WORKFLOW TỰ CỨU LỖI POLICY V5 (`v5_heal_policy`)

Khi người dùng báo có cảnh bị Google Flow chặn chính sách hoặc yêu cầu chạy cứu lỗi Policy cho một tập phim trong `story_mode/projects/<Ten_Series>/<Ten_Tap>/`, thực hiện nghiêm ngặt 4 bước sau:

## Bước 1 — Lọc các cảnh bị lỗi Policy từ `pipeline_state.json`
1. Đọc file `story_mode/projects/<Ten_Series>/<Ten_Tap>/pipeline_state.json`.
2. Lọc ra toàn bộ các cảnh thỏa mãn một trong hai điều kiện:
   - `image_status == "policy_error"`
   - `video_status == "policy_error"`
3. Liệt kê rõ danh sách `id`, `asset_type`, giai đoạn bị chặn (`images` hay `videos`) và nội dung trường `error`. Nếu không có cảnh nào mang trạng thái `"policy_error"`, báo rõ cho người dùng và dừng lại.

## Bước 2 — Chẩn đoán nguyên nhân trên `storyboard.json` & ảnh debug
1. Đọc `storyboard.json` của tập phim tại các cảnh bị lỗi, đối chiếu `image_prompt`, `motion_prompt`, `speech` và bối cảnh các cảnh liền kề (`id - 1`, `id + 1`).
2. Nếu có ảnh chụp màn hình debug trong `automation/debug/policy_scene_{id:03d}_{mode}.png`, kiểm tra thêm thông báo từ chối thực tế của Google Flow để xác định từ khóa hoặc hình ảnh kích hoạt bộ lọc.

## Bước 3 — Viết mềm lại Prompt theo hướng điện ảnh an toàn
Sửa trực tiếp `image_prompt` (nếu lỗi ở `images`) và/hoặc `motion_prompt` (nếu lỗi ở `videos`) theo các nguyên tắc bắt buộc:
1. **Loại bỏ triệt để yếu tố nhạy cảm**: không mô tả súng đạn, vũ khí cận cảnh, đụng độ/đối đầu trực diện, thương tích, khói lửa cháy nổ dữ dội, hành vi bạo lực hay tiếng la hét.
2. **Tránh vi phạm định danh nhân vật thật**: với cảnh `static_image` mang chất liệu tư liệu, không ghép tên riêng nhân vật có thật với từ `photograph`; chuyển sang góc sau vai, góc nghiêng bên bản đồ, bàn chỉ huy, hiện vật bảo tàng, bóng đổ hoặc toàn cảnh địa hình.
3. **Chuyển sang ngôn ngữ ẩn dụ & ánh sáng điện ảnh**: thay cảnh giao tranh/pháo kích bằng ánh sáng hổ phách rọi từ xa (`warm amber light glowing across distant misty ridges`), cột khói trắng bốc nhẹ (`rising plumes of white mist`), bóng người hành quân trong sương (`silhouetted figures advancing steadily through morning fog`), hoặc cận cảnh đôi tay/bản đồ/ánh mắt kiên nghị (`closed mouth, no lip movement`).
4. **Bảo toàn 100% sử liệu & tính liền mạch**: không làm sai lệch nội dung lời thoại `speech`, giữ đúng `Style & Character Bible`, giữ đuôi `vertical 9:16, no text, no letters, no watermark` (với `image_prompt`) và hướng chuyển động mượt nối tiếp với cảnh trước/sau.

## Bước 4 — Kiểm định lại, đặt lại trạng thái & xuất lệnh chạy bù
1. Lưu `storyboard.json` đã sửa và chạy lệnh kiểm định:
   ```powershell
   .venv\Scripts\python automation/scripts/validate_storyboard.py "story_mode/projects/<Ten_Series>/<Ten_Tap>/storyboard.json"
   ```
   Tự sửa cho đến khi **PASS 100% (0 ERROR)**.
2. Mở `pipeline_state.json` và **chỉ đặt lại trạng thái của các cảnh vừa sửa** (giữ nguyên toàn bộ các cảnh `"done"` khác):
   - Nếu sửa lỗi ảnh: đặt `"image_status": "pending"`, `"error": ""`.
   - Nếu sửa lỗi video: đặt `"video_status": "pending"`, `"error": ""`.
3. Báo cáo ngắn gọn từng cảnh đã viết lại theo hướng nào, đồng thời in sẵn lệnh chạy bù chính xác cho người dùng:
   - Nếu có cảnh lỗi ảnh:
     ```powershell
     .venv\Scripts\python automation/scripts/flow_bot.py "story_mode/projects/<Ten_Series>/<Ten_Tap>/storyboard.json" --mode images --scenes <id1,id2,...>
     ```
   - Nếu có cảnh lỗi video:
     ```powershell
     .venv\Scripts\python automation/scripts/flow_bot.py "story_mode/projects/<Ten_Series>/<Ten_Tap>/storyboard.json" --mode videos --scenes <id1,id2,...>
     ```
