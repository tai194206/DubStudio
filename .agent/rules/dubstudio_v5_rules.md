# QUY TẮC BẮT BUỘC: PIPELINE LỊCH SỬ TIKTOK V5.2 (DUBSTUDIO v3.2 + ƯU TIÊN 100% TƯ LIỆU THẬT + GOOGLE FLOW)

Mọi tác vụ chia tập, viết kịch bản, thiết kế hình ảnh, motion và xuất JSON cho series lịch sử PHẢI tuân thủ tuyệt đối các luật dưới đây:

## 1. Quy chuẩn đường dẫn & Không phá lõi DubStudio
- TUYỆT ĐỐI không sửa mã nguồn trong `core/`, `gui/`, `story_mode/story/`, `tests/` khi đang chạy quy trình sản xuất nội dung.
- File kiến thức đầu vào: `automation/inputs/<ten_su_kien>.txt`
- Thư mục Series: `story_mode/projects/<Ten_Series>/`
  - Chứa `series_plan.md` (bản đồ toàn bộ series từ Giai đoạn 0, kèm đánh giá kho tư liệu thật và chốt Hạng tỉ lệ `0_100` / `30_70` / `50_50` / `70_30`).
  - Chứa `Caption.md` (caption TikTok đồng bộ tất cả các tập của series).
- Thư mục từng tập: `story_mode/projects/<Ten_Series>/<Ten_Tap>/` (không dấu, dùng gạch dưới, vd: `Dien_Bien_Phu_Tap_04`).
  - `assets/` (BẮT BUỘC đúng chính tả `assets`, không viết `acess`; chứa `full_audio.mp3`, `{id:03d}.mp4` cho cảnh `flow_video`, `{id:03d}.jpg` cho cảnh `static_image`).
  - `raw_images/` (chứa toàn bộ ảnh gốc `{id:03d}.png` gen dự phòng từ Nano Banana Pro).
  - `storyboard.json` (chuẩn `core/story_schema.py` v3.0 kèm `"asset_ratio_tier"` — tương thích cả `shot_mode: "scene"` và `"planned"`).
  - `archive_checklist.md` (danh sách các cảnh `static_image` kèm mô tả nội dung cần tìm, từ khóa tìm ảnh tư liệu thật tiếng Việt & tiếng Anh, và tên file đích `assets/{id:03d}.jpg`).
  - `elevenlabs.txt` (mỗi câu thoại 1 dòng, sạch 100%, không `...`, không chú thích).
  - `timeline_script.txt` (kịch bản có mốc `[HH:MM:SS.mmm -> HH:MM:SS.mmm]`).
  - `pipeline_state.json` (theo dõi trạng thái từng cảnh cho bot Playwright).

## 2. Quy chuẩn Kịch bản, Hook 3s & Giữ chân Viral V5.2 (`core/story_pipeline.py` v3.2)
- **Luật Hook 3 giây đầu (Cảnh 1 — BẮT BUỘC):**
  - **Độ dài lời thoại Cảnh 1:** Ngắn gọn **8 – 13 từ** (đọc xong trong ~2.5–3.2 giây ở tốc độ `1.20x`) để cắt cảnh cực nhanh ngay đầu video.
  - **Công thức nội dung:** Mở thẳng bằng câu hỏi **"Tại sao...?"**, một nghịch lý quân sự, con số gây sốc hoặc cú bẻ lái đắt nhất tập.
  - **CẤM TUYỆT ĐỐI** các câu mở đầu nhàm chán ở Cảnh 1: *"Tập trước..."*, *"Ở tập trước..."*, *"Hôm nay..."*, *"Chào mừng..."*, *"Xin chào..."*. (Nếu cần nối mạch tập trước thì gói gọn trong 1 câu ngắn đặt ở **Cảnh 2**).
  - **Hình ảnh & Âm thanh Cảnh 1:** Chọn ảnh tư liệu/khung hình kịch tính nhất tập (kèm `zoom_in` dứt khoát nếu là `static_image`, hoặc clip hành động mạnh nhất nếu có `flow_video`) và **BẮT BUỘC** gắn `"sfx"` mạnh ngay `0.00s` (dùng enum hợp lệ DubStudio: `boom`, `whoosh`, `alarm`).
- **Luật Giữ chân & Tương tác Viral V5.2:**
  - **Độ dài tối ưu tăng Completion Rate:** Mỗi tập gói gọn **20 – 22 cảnh** (~300–350 từ, thời lượng khoảng **80–100 giây** sau khi xuất ở tốc độ `1.20x`), trừ khi sự kiện quá lớn cần 24–26 cảnh (~380–460 từ).
  - **Nhịp từng cảnh (Cảnh 2 trở đi):** **13 – 18 từ** (`4.2 – 5.5 giây` đọc ở `1.00x`), khớp `Smart Clip Fit` của DubStudio.
  - **Cú bẻ nhịp giữa tập (Mid-video Re-hook):** Tại khoảng **Cảnh 9–11** (tương ứng giây 35–45 — điểm dễ tụt người xem nhất), bắt buộc có 1 bước ngoặt/câu lật kèo bất ngờ kèm `"sfx"` chuyển hồi rõ rệt (`boom` / `whoosh` / `alarm`).
  - **Đặc sản Bản đồ chiến thuật:** Mỗi tập chiến dịch quân sự bắt buộc có **2 – 3 cảnh bản đồ chiến thuật / sơ đồ mũi tên tiến công** độ tương phản cao, đặt đúng lúc phân tích thế trận.
  - **Cảnh cuối (Cliffhanger + CTA mồi bình luận):** Ngoài câu dẫn sang tập sau, bắt buộc chèn 1 câu hỏi mở dễ trả lời hoặc lời tri ân kích thích người xem bình luận (vd: *"Theo bạn, nếu không có bước đi này cục diện sẽ ra sao? Bình luận bên dưới nhé!"*).
  - **Phân bổ Sound Design (`sfx`) có chủ đích:** Bố trí **5 – 8 hiệu ứng `sfx` đắt giá** tại đúng các điểm: Cảnh 1 (Hook), Re-hook giữa tập (Cảnh 9–11), 2–3 cảnh cao trào chiến đấu/pháo nổ, và Cảnh cuối (tránh rải đều nhạt nhòa).
- Tự thích ứng phong cách kể chuyện (`A`: Giải mã nghịch lý/Trinh thám, `B`: Cold Open/Sử thi hào hùng, `C`: Hồ sơ giải mật/Phân tích đa chiều).
- Bám sát 100% file kiến thức `.txt`, không bịa đặt sự kiện hay lời thoại ngoài sử liệu.
- Chuẩn hóa lời đọc cho ElevenLabs & Whisper (`highlight_3words` / `pop_2words`):
  - KHÔNG dùng dấu ba chấm `...`, ngoặc đơn chú thích hay ký tự lạ trong lời thoại.
  - Có thể dùng chữ số tự nhiên (vì `story_pipeline.py` đã hỗ trợ `_int_to_vi_norms`), nhưng tên riêng nước ngoài khó đọc PHẢI viết phiên âm tiếng Việt thống nhất toàn tập (vd: `Đờ Cát`, `Na-va`, `Giơ-ne-vơ`, `Pi-rốt`).

## 3. Quy chuẩn Hình ảnh Lai V5.2 (4 Hạng ưu tiên Tư liệu thật + Google Flow dự phòng 0đ)
- **Hệ thống 4 Hạng tỉ lệ ưu tiên (BẮT BUỘC đánh giá độ phong phú tư liệu lịch sử trước khi chia cảnh):**
  - **Hạng 1 — `0_100` (Mặc định ưu tiên cao nhất: 0%–15% `flow_video` / 85%–100% `static_image`):** Áp dụng cho các sự kiện cận đại – hiện đại có kho ảnh tư liệu phong phú (như Điện Biên Phủ 1954, Kháng chiến chống Mỹ...).
    - Mục tiêu săn **100% ảnh tư liệu thật** qua `fetch_archive_images.py` (Wikimedia Commons / ảnh chiến trường, nhân vật, hiện vật gốc).
    - Cảnh hiếm không tìm được ảnh thật thì `image_prompt` dự phòng (`0 tín dụng` từ Google Flow) **BẮT BUỘC** viết theo phong cách **ảnh phim tư liệu lịch sử thật** (*authentic 1954 archival documentary photograph, black-and-white or subtle aged sepia, candid wartime photojournalism, natural 35mm film grain, historical realism*) — **CẤM** phong cách minh họa 3D/AI bóng loáng để đồng bộ 100% với ảnh thật và tránh bị quét AI.
    - Có thể dùng **0 clip AI** (thuần tư liệu + bản đồ chiến thuật, `0 tín dụng`) hoặc tối đa 1–3 clip AI điểm đinh nếu thực sự cần chuyển động cao trào.
    - Không giới hạn số cảnh `static_image` đứng liền nhau, nhưng **BẮT BUỘC** 100% cảnh tĩnh không dùng `"static"` và 2 cảnh liền kề **không được trùng kiểu `ken_burns`**.
  - **Hạng 2 — `30_70`** (~30% AI / 70% thật, biên độ 25%–38% AI, tối đa **4** cảnh `static_image` liền nhau): Dùng khi tư liệu thật ở mức khá nhưng cần thêm clip động minh họa diễn biến.
  - **Hạng 3 — `50_50`** (~50% / 50%, biên độ 45%–55% AI, tối đa **3** cảnh `static_image` liền nhau): Áp dụng cho giai đoạn 1911–1945 có ảnh chân dung/báo chí nhưng thiếu cận cảnh sự kiện.
  - **Hạng 4 — `70_30`** (~70% AI / 30% thật, biên độ 65%–75% AI, tối đa **2** cảnh `static_image` liền nhau): Chỉ dự phòng cho thời cổ đại, phong kiến gần như không có ảnh thật.
  - BẮT BUỘC khai báo `"asset_ratio_tier": "0_100" | "30_70" | "50_50" | "70_30"` ở root `storyboard.json` (`core/story_schema.py` đã xác nhận tương thích 100%).
- **Luật chuyển động & chống nhàm chán cho `static_image`:**
  - **Bắt buộc đổi chiều `ken_burns`:** Hai cảnh `static_image` đứng liền kề nhau TUYỆT ĐỐI KHÔNG dùng trùng một kiểu `ken_burns` và không để `"static"` (luân phiên `zoom_in`, `zoom_out`, `pan_left`, `pan_right` để khung hình luôn có chuyển động).
  - Ở các hạng có `flow_video` (`30_70`, `50_50`, `70_30`): ưu tiên dồn `flow_video` vào Cảnh 1 Hook, Re-hook/cao trào giữa tập và Cảnh cuối.
- **Cơ chế lấy nguồn linh hoạt cho `static_image` (Ảnh thật ưu tiên + Flow dự phòng 0đ):**
  - Mỗi tập tạo thêm file `archive_checklist.md` liệt kê riêng toàn bộ cảnh `static_image` kèm mô tả nội dung cần tìm + từ khóa tìm ảnh tư liệu thật (tiếng Việt & tiếng Anh) và tên file đích `assets/{id:03d}.jpg`.
  - Vẫn BẮT BUỘC viết đủ `image_prompt` phong cách ảnh phim tư liệu/bản đồ cổ trong `storyboard.json` cho toàn bộ cảnh `static_image` để nếu cảnh nào chưa có ảnh thật thì `flow_bot.py` tự gen bù (`0 tín dụng` qua Nano Banana Pro), còn tấm nào đã có sẵn trong `assets/{id:03d}.jpg` thì bot tự động `SKIP_EXISTING_ASSET`.
- **Cấu trúc `image_prompt` (45 – 65 từ tiếng Anh):**
  - Đưa góc máy + chủ thể + hành động lên ĐẦU CÂU (trung cảnh, cận cảnh thần thái, góc thấp oai hùng).
  - Mô tả chính xác trang phục, đạo cụ, bối cảnh lịch sử Việt Nam đúng niên đại.
  - Khóa khẩu hình nhân vật: `closed mouth` (khép miệng, ánh mắt kiên nghị/trầm tư) để tránh lỗi mấp máy môi.
  - Luôn chốt đuôi: `vertical 9:16, no text, no letters, no watermark`.
  - Cảnh `static_image`: bắt buộc dùng chất liệu ảnh phim tư liệu (`authentic archival documentary photograph, black-and-white or subtle aged sepia, candid wartime photojournalism, natural 35mm film grain, historical realism`) hoặc bản đồ quân sự cổ (`vintage military topographic paper map, high-contrast tactical arrows, aged archive texture`). Tránh ghép tên riêng nhân vật có thật với từ `photograph` gây chặn policy Google Flow (ưu tiên góc sau vai, góc nghiêng bên bản đồ, hiện vật, địa hình).

## 4. Quy chuẩn Motion & Luật An Toàn Google Flow (Veo 3.1 - Lite — 10 tín dụng)
- Chỉ viết `motion_prompt` (20 – 32 từ tiếng Anh) cho cảnh `asset_type == "flow_video"`. Cảnh `static_image` để `"motion_prompt": ""`.
- KHÔNG tả lại quần áo/bối cảnh tĩnh; KHÔNG ghi số giây hay âm thanh/giọng nói trong prompt (clip `.mp4` sẽ bị mute hoàn toàn bằng FFmpeg).
- **DANH SÁCH TỪ CẤM TUYỆT ĐỐI (trên cả `image_prompt` lẫn `motion_prompt`):**
  `blood, gore, kill, killing, dead, corpse, die, suicide, shoot, shooting, firing gun, gunshot, stab, slash, bomb, bombing, explode, explosion, blast, detonate, weapon, rifle, grenade, screaming, carnage, warzone, attack, assault, decapitation, brutal wounds`.
  - Quy đổi cháy nổ/pháo kích -> `bright amber light flashes in the distance, rising plumes of white smoke, drifting dust and embers`.
  - Quy đổi xung phong -> `silhouetted figures advancing steadily through misty smoke, dynamic forward tracking movement`.
- Cấm nhân vật nói/hét (`no lip movement` ở cảnh cận mặt).
- Chuyển động mượt khớp `xfade` & `Smart Clip Fit`: dùng `slow steady push-in`, `gentle horizontal tracking pan`, `slow majestic zoom-out`, `slow low-angle rise`. Tránh `whip pan` và giữ nối hướng chuyển động mượt giữa Cảnh N -> Cảnh N+1.

## 5. Quy chuẩn Schema `storyboard.json` (Khớp 100% `core/story_schema.py`)
- Dùng `"version": "3.0"` và `"title"` (không dấu, gạch dưới) — TUYỆT ĐỐI KHÔNG dùng `"project_title"`.
- Mảng `"scenes"` dùng khóa `"id"` (số nguyên `1, 2, 3...`) — TUYỆT ĐỐI KHÔNG dùng `"scene_id"`.
- `"locations"`: mỗi cảnh 1 key riêng ánh xạ 1-1: `flow_video` trỏ `"{id:03d}.mp4"`, `static_image` trỏ `"{id:03d}.jpg"`.
- `"sfx"` CHỈ dùng 1 trong 7 giá trị: `["none", "whoosh", "boom", "pop", "typing", "alarm", "ship_horn"]`.
- `"motion"` và `"ken_burns"` đều phải có và CHỈ dùng 1 trong 5 giá trị: `["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]`.
- `"beats"` đủ trường: `id`, `text`, `location`, `intensity` (1–3), `intent` trong `["establish", "explain", "reveal", "emotion"]`, `sfx`.
- `"on_screen_text"`: chữ IN HOA ngắn 3–6 từ (không đè watermark góc trên trái).
- **ĐỒNG BỘ TUYỆT ĐỐI:** `beats[i].text === scenes[i].speech` từng ký tự; `"full_script_for_elevenlabs"` là chuỗi ghép toàn bộ `scenes[i].speech` cách nhau đúng 1 dấu cách.
- Nếu có `automation/scripts/validate_storyboard.py`, BẮT BUỘC chạy lệnh kiểm tra sau khi xuất JSON và tự sửa đến khi PASS 100%.

## 6. Quy chuẩn `Caption.md` cho TikTok
- Mỗi tập trong `story_mode/projects/<Ten_Series>/Caption.md` dùng đúng mẫu:
  `Phần {X} | {Tên Series ngắn}: {Hook/Tiêu đề tập hấp dẫn} #LearnOnTikTok #hoccungtiktok #dongluc #viral #xuhuong #lichsuVietNam #lichsu #tuhaodantoc #xuhuongtiktok #trending #tiktok #{hashtag_su_kien}`
