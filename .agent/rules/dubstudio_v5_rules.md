# QUY TẮC BẮT BUỘC: PIPELINE LỊCH SỬ DUAL-DELIVERY V5.8 + V5.5 (UNIFIED COLD SLATE-CHARCOAL HOUSE LOOK `restored_doc_still` + TIKTOK 9:16 + YOUTUBE 16:9 SONG SONG + DUBSTUDIO v3.2 + CHỐNG BÓNG NHỰA AI + GOOGLE FLOW)

Mọi tác vụ chia tập, viết kịch bản, thiết kế hình ảnh, motion và xuất JSON cho series lịch sử PHẢI tuân thủ tuyệt đối các luật dưới đây:

## 0. MẶC ĐỊNH BẮT BUỘC CHO MỌI DỰ ÁN & SERIES MỚI (V5.8 + V5.5 — GLOBAL DEFAULT)
Năm quy chuẩn dưới đây được khóa **mặc định vĩnh viễn** trên toàn hệ thống (`settings.json`, `core/config.py`, `core/house_look.py`, `core/anti_ai_filter.py`, `flow_bot.py`, `run_episode.py`, `validate_storyboard.py`) và tự động áp dụng cho mọi thời kỳ lịch sử mà không cần nhắc lại cấu hình:
1. **Tách rạch ròi 2 lớp tài sản (House Look thống nhất V5.8):**
   - **Lớp A — Tư liệu lịch sử thật (`archive_sources.json`):** Khóa nguyên vẹn file gốc và **SHA-256 100%** (tuyệt đối không sửa pixel trên đĩa, không auto-crop watermark, không tô màu). Chỉ phủ một **lớp hòa tông cực nhẹ non-destructive lúc render (~15%)** (`eq=contrast=1.04:brightness=-0.02` + sắc lạnh slate ~10–15%) để chuyển cảnh mượt với ảnh AI, đồng thời phủ tông lạnh tối cho nền mờ `Blurred Pillar-box`.
   - **Lớp B — Cảnh AI gen bù (Google Flow — Mặc định toàn hệ thống `visual_preset: "restored_doc_still"` — chuẩn V5.8 Unified Cold Slate-Charcoal House Look):** Toàn bộ cảnh ban ngày, trong hầm lẫn ban đêm/chiến đấu dùng chung **1 chữ ký thị giác duy nhất**: nền **xám chì nghiêng lạnh (`cold slate-charcoal near-monochrome` tẩy bạc tối sâu)**, **bầu trời u ám phủ khói (`heavy smoke-darkened overcast sky, no bright white sky`)**. Màu ấm CHỈ được đến từ nguồn sáng thật (tia nắng thực, lửa, chớp pháo), **TUYỆT ĐỐI KHÔNG** tô vàng giả lên súng/đạo cụ hay tự cắm đuốc cháy.
2. **House Look 2 tầng (`core/house_look.py`), Soft LAB Match & Hạt phim đồng nhất:**
   - **Tầng 1 (Global Default):** Dùng profile chuẩn lưu từ `008_v57.jpg` tại `core/assets/house_look_default.jpg` (và `.json`) để mọi dự án sau tự động chung 1 chữ ký màu.
   - **Tầng 2 (Project Override):** Hỗ trợ tùy chọn `"look_reference"` trong `storyboard.json` nếu một dự án muốn chọn khung hero riêng (hoặc `"none"` để tắt).
   - Ngay sau khi tải ảnh AI từ Flow về (`process_flow_downloaded_image`), ngoài `crop_flow_watermark(1.04)`, hệ thống chạy:
     1. **Khớp màu mềm CIE LAB** (`strength=0.64`, dải `0.55–0.70`) giữ chi tiết vùng tối ban ngày và giữ ấm đốm lửa thật.
     2. **Gradient ND nhẹ phần trời mép trên** (`top_nd_strength=0.20`, dải `0.15–0.25`) ưu tiên dìm vùng trời trắng sáng, giữ nguyên khuôn mặt tối cận cảnh.
     3. **Tiền làm mịn siêu nhẹ (10%) + hạt phim 35mm Tri-X đơn sắc & bụi lưu trữ đồng nhất** (giữ trọn 90% kết cấu vải/da gai góc, không bệt nhựa AI).
     4. **Kiểm định độ đồng bộ tự động (`audit_image_against_house_look`)**: đo `L`, `b`, `topL`, `C*`, chấm điểm `0–100` và gắn cờ cảnh lệch chuẩn ngay sau khi tải.
3. **Render FFmpeg chuẩn House Look & Chống Double-Grading (`anti_ai_filter.py`):**
   - **Chống phủ màu & phủ hạt 2 lần (Double-Grading Guard):** Khi `flow_bot.py` đã áp `apply_house_look_to_image` lúc tải ảnh về, cảnh đó được đánh dấu `"house_look_applied": true` trong `pipeline_state.json`. Lúc render (`story_pipeline.py` & `series_compiler.py`), `build_ai_scene_depolish_vf(..., house_look_applied=True)` tự động chuyển sang chế độ **pass-through siêu nhẹ** (chỉ giữ scale+crop vi mô & `vignette` nhẹ, **KHÔNG** giảm saturation, **KHÔNG** dìm curves, **KHÔNG** phủ thêm colorbalance hay noise lần 2).
   - **Tương thích ngược cho ảnh AI cũ chưa qua `house_look` (`house_look_applied=False`):** vẫn áp đầy đủ `DEPOLISH_SPECS` V5.8 (`hqdn3d=0.6..1.0`, `saturation 0.62/0.52/0.44`, `curves` low-key, `colorbalance` slate-blue, `vignette=PI/7..PI/5`, `noise=alls=5/7/9`).
   - Era-Agnostic (không khóa cứng `1954` hay `Viet Minh`), ưu tiên mood ban ngày (`morning`, `daylight`, `overcast`), vẫn tôn trọng `--no-archival-photo` / `--visual-preset raw` và `"archival_photo": false`.
4. **Lời thoại chuẩn TTS tiếng Việt ngay từ đầu:**
   - Tuyệt đối không dùng ký hiệu viết tắt, đơn vị ký hiệu hay số thập phân dễ đọc vấp trong `speech` và `beats[].text` (`cm` → `xăng-ti-mét`, `°` → `độ`, `0,8` → `0 phẩy 8`, `2,5` → `2 mét rưỡi`).
5. **Tự động săn tư liệu đa nguồn V5.5 & Kiểm định an toàn trước khi render:**
   - Quét trước `vn_press`, `wikimedia`, `internet_archive`, `nara`, khóa SHA-256 ảnh thật + audio gốc, và chạy `validate_storyboard.py` đạt **`0 ERROR`** trước khi sản xuất.

## 1. Quy chuẩn đường dẫn & Không phá lõi DubStudio
- TUYỆT ĐỐI không sửa mã nguồn trong `core/`, `gui/`, `story_mode/story/`, `tests/` khi đang chạy quy trình sản xuất nội dung.
- File kiến thức đầu vào: `automation/inputs/<ten_su_kien>.txt`
- Thư mục Series: `story_mode/projects/<Ten_Series>/`
  - Chứa `series_plan.md` (bản đồ toàn bộ series từ Giai đoạn 0, kèm đánh giá kho tư liệu thật, **chốt cứng 1 `frame_preset` duy nhất toàn series**, nhánh phong cách theo thời kỳ và Hạng tỉ lệ `0_100` / `30_70` / `50_50` / `70_30`).
  - Chứa `Caption.md` (caption TikTok đồng bộ tất cả các tập của series).
- Thư mục từng tập: `story_mode/projects/<Ten_Series>/<Ten_Tap>/` (không dấu, dùng gạch dưới, vd: `Dien_Bien_Phu_Tap_04`).
  - `assets/` (BẮT BUỘC đúng chính tả `assets`, không viết `acess`; chứa `full_audio.mp3`, `{id:03d}.mp4` cho cảnh `flow_video`, `{id:03d}.jpg` cho cảnh `static_image`).
  - `raw_images/` (chứa toàn bộ ảnh gốc `{id:03d}.png` gen dự phòng từ Nano Banana Pro).
  - `storyboard.json` (chuẩn `core/story_schema.py` v3.0 kèm `"frame_preset"` và `"asset_ratio_tier"` — tương thích cả `shot_mode: "scene"` và `"planned"`).
  - `archive_checklist.md` (danh sách các cảnh `static_image` kèm mô tả nội dung cần tìm, từ khóa tìm ảnh/video tư liệu thật tiếng Việt, Anh, Pháp và tên file đích `assets/{id:03d}.jpg` / `.mp4`).
  - `fact_check.md` (**BẮT BUỘC**: bảng kiểm chứng mọi mốc thời gian, quân số, tên đơn vị, thương vong, nhân vật, địa danh xuất hiện trong kịch bản kèm nguồn đối chiếu).
  - `archive_sources.json` (nhật ký nguồn, tác giả, giấy phép, URL và mốc cắt video do `automation/scripts/fetch_archive_media.py` tự ghi khi nạp tư liệu thật vào `assets/`).
  - `elevenlabs.txt` (mỗi câu thoại 1 dòng, sạch 100%, không `...`, không chú thích).
  - `timeline_script.txt` (kịch bản có mốc `[HH:MM:SS.mmm -> HH:MM:SS.mmm]`).
  - `pipeline_state.json` (theo dõi trạng thái từng cảnh cho bot Playwright).

## 1B. Luật Chính xác Lịch sử Tuyệt đối (Zero-Fabrication) & An toàn Bản quyền (Media Authenticity)
- **Khóa sự thật lịch sử (Zero-Fabrication Policy):**
  - **TUYỆT ĐỐI KHÔNG** bịa đặt ngày tháng, quân số, tên đơn vị, thương vong, địa danh hay câu nói của nhân vật lịch sử.
  - Mọi sự kiện/con số đưa vào lời thoại (`elevenlabs.txt`) phải bám sát tài liệu `.txt` đầu vào hoặc chính sử đã được công nhận rộng rãi. Chi tiết nào không chắc chắn 100% **BẮT BUỘC loại bỏ**, không phóng đại hay suy diễn.
  - Mỗi tập bắt buộc xuất file `fact_check.md` liệt kê bảng đối chiếu từng mốc thời gian, con số, nhân vật, đơn vị và địa danh kèm nguồn kiểm chứng.
- **Khóa tư liệu thật & bản quyền V5.5 (Media Authenticity Policy — Đa nguồn):**
  - Ưu tiên tuyệt đối ảnh & video thuộc phạm vi công cộng / giấy phép mở qua `automation/scripts/fetch_archive_media.py` với cờ `--sources wikimedia,internet_archive,nara` và `--media-type image|video|all`:
    - **Wikimedia Commons (`wikimedia`):** Ảnh & video tư liệu Public Domain / CC0 / CC BY-SA.
    - **Internet Archive (`internet_archive` — `archive.org`):** Cả ảnh lịch sử lẫn video thời sự gốc Public Domain (`.mp4`, vd: *Universal Newsreels* / phim thời sự Đông Dương 1950s).
    - **NARA (`nara` — `catalog.archives.gov`):** Ảnh quân sự độ phân giải cao từ Lưu trữ Quốc gia Hoa Kỳ (`Public Domain — U.S. National Archives`).
    - **Báo chí & Bảo tàng Việt Nam (`vn_press` — `qdnd.vn`, `baotanglichsu.vn`, `btlsqsvn.org.vn`, `vnanet.vn`, `nhandan.vn`, `vietnamplus.vn`):** Dùng `--urls <url1> <url2>` hoặc `--sources vn_press --query "..."` để bóc ảnh tư liệu lịch sử 1945–1979 (`>= 600px`) kèm chú thích vào `<project_dir>/staging_press/` + `contact_sheet.jpg` + `manifest.json`. Duyệt trực quan rồi nạp cảnh chọn bằng `--promote <id>:<scene_num>` (ghi đúng nhãn `"Editorial / Press Archive (Credit Required)"`, không ghi sai thành Public Domain).
  - **CẤM TUYỆT ĐỐI** lấy clip/ảnh dính logo đài truyền hình, watermark đè giữa ảnh hoặc cắt từ phim điện ảnh/phóng sự VTV dễ dính Content ID (`VTV`, `INA`, `AP`, `Reuters`, `Getty`, `Shutterstock`, `British Pathé` có đóng dấu).
  - **CẤM** dùng nhầm tư liệu sai thời kỳ (ví dụ: tuyệt đối không dùng hình ảnh giai đoạn 1965–1975 cho Điện Biên Phủ 1954).
  - **Khi cảnh khan hiếm tư liệu thật:** ưu tiên dùng **Bản đồ chiến thuật / sơ đồ mặt cắt kỹ thuật / cảnh núi rừng, bối cảnh biểu tượng**. **TUYỆT ĐỐI KHÔNG** dùng AI tạo giả ảnh chụp nhân vật lịch sử thật làm những việc không có thật trong lịch sử (tránh vi phạm chính sách nội dung tổng hợp gây hiểu lầm của TikTok).

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

## 3. Quy chuẩn Hình ảnh Lai & Chống Bóng Nhựa AI V5.4 (Khóa `frame_preset` + Phân nhánh Thời kỳ)
- **Ánh xạ chuẩn 5 nút tỉ lệ thực tế trên Google Flow (`16:9`, `4:3`, `1:1`, `3:4`, `9:16`):**
  - `landscape_16_9` → chọn `"16:9"` (`1920x1080`, dùng cho YouTube 16:9 `--youtube-16x9` và series ngang).
  - `framed_4_5` → ưu tiên `"3:4"` (nút sát khung `4:5` nhất trên UI Flow, giữ bố cục chặt hơn `9:16`; fallback `"9:16"`), thành phẩm chuẩn hóa `1080x1350`.
  - `framed_1_1` → ưu tiên `"1:1"` (fallback `"4:3"`, rồi `"16:9"`), thành phẩm chuẩn hóa `1080x1080`.
  - `portrait_9_16` → chọn `"9:16"` (`1080x1920`).
- **Khóa `frame_preset` cố định toàn series (BẮT BUỘC):**
  - Khi lập `series_plan.md`, phải chốt rõ **1 `frame_preset` duy nhất** cho cả series (`framed_4_5`, `framed_1_1`, `portrait_9_16`, hoặc `landscape_16_9`) và ghi đồng bộ vào trường top-level `"frame_preset"` của mọi `storyboard.json` trong series (tập cũ không khai báo mặc định `portrait_9_16`).
  - Khóa cú pháp bố cục trong `image_prompt` theo đúng khung đã chốt:
    - `framed_4_5`: `4:5 vertical framing, balanced headroom, subject centered within 4:5 safe zone, edge-to-edge full-bleed, no white borders`
    - `framed_1_1`: `1:1 square archival framing, centered subject, edge-to-edge full-bleed, no white borders`
    - `portrait_9_16`: `vertical 9:16, edge-to-edge full-bleed, no white borders`
    - `landscape_16_9`: `16:9 widescreen framing, balanced horizontal composition, edge-to-edge full-bleed, no white borders`
- **Phân nhánh phong cách theo thời kỳ lịch sử:**
  - **Sử cận/hiện đại (1945–1979) — QUY TẮC 3 TẦNG CHỐNG "QUÁ AI / TRANH VẼ" (BẮT BUỘC):**
    - **Tầng 1 (Tư liệu thật đúng sự kiện):** Ưu tiên tối đa ảnh/clip lưu trữ thật qua `automation/scripts/fetch_archive_media.py` (mặc định `--exclude-modern` lọc bỏ ảnh du lịch/tượng đài/hiện đại).
    - **Tầng 2 (Tư liệu thật cùng bối cảnh / thập niên):** Nếu không có ảnh đúng khoảnh khắc đó, ưu tiên ảnh tư liệu thật cùng chiến dịch / mặt trận / giai đoạn lịch sử trước khi phải gọi AI.
    - **Tầng 3 (AI gen bù chuẩn V5.8 Unified Cold Slate-Charcoal House Look `restored_doc_still` — BẬT MẶC ĐỊNH trong `flow_bot.py` & `run_episode.py`):** Mọi cảnh AI gen bù PHẢI đồng bộ chung **1 chữ ký màu xám chì nghiêng lạnh tẩy bạc tối sâu** (`heavy bleach-bypass, unified cold slate-charcoal near-monochrome shadows, heavy smoke-darkened overcast sky, no bright white sky`), màu ấm chỉ đến từ tia nắng/lửa thực (không tô vàng giả lên đạo cụ hay tự cắm đuốc cháy), đi qua soft LAB color matching (`core/house_look.py`) + top ND gradient + hạt phim 35mm Tri-X đồng nhất. Ảnh thật trong `archive_sources.json` giữ nguyên file gốc & SHA-256, chỉ hòa tông nhẹ ~15% lúc render. Chỉ gắn `"archival_photo": false` ở cấp cảnh trong `storyboard.json` nếu đó là cảnh sơ đồ mặt cắt kỹ thuật/bản đồ giấy cổ.
    - **CẤM TUYỆT ĐỐI** phong cách minh họa 2D (`2D illustration`, `2D animation`, `cel-shading`, `clean lineart`, `digital painting`, `painterly brushstrokes`), màu rực HDR (`cinematic HDR`, `orange and teal`), bóng nhựa `glossy`, viền giấy giả cổ (`yellowed paper border`, `vintage frame`, `fake scan frame`), và hạn chế tối đa cận mặt người AI (ưu tiên góc rộng, góc sau vai `over-the-shoulder`, hoặc góc nghiêng tự nhiên).
  - **Sử cổ/trung đại (không có ảnh chụp):** TUYỆT ĐỐI KHÔNG ép AI giả ảnh chụp người thật; chuyển sang hệ mỹ thuật **tranh tư liệu bảo tàng / tranh sơn dầu cổ điển / tranh mực tàu giấy dó** xen kẽ bản đồ cổ và ảnh thật di tích/hiện vật (săn Wikimedia bằng cờ `--include-sites`, gắn `"archival_photo": false` hoặc `--visual-preset raw` khi dùng tranh cổ).
- **Hệ thống 4 Hạng tỉ lệ ưu tiên (BẮT BUỘC đánh giá độ phong phú tư liệu lịch sử trước khi chia cảnh):**
  - **Hạng 1 — `0_100` (Mặc định ưu tiên cao nhất: 0%–15% `flow_video` / 85%–100% `static_image`):** Áp dụng cho các sự kiện cận đại – hiện đại có kho ảnh tư liệu phong phú (như Điện Biên Phủ 1954, Kháng chiến chống Mỹ...).
    - Mục tiêu săn **100% ảnh & clip tư liệu thật** qua `automation/scripts/fetch_archive_media.py` (Wikimedia Commons + Internet Archive `archive.org`, duyệt contact sheet loại watermark/sai thời kỳ trước khi nạp `assets/`).
    - Cảnh hiếm không tìm được ảnh/clip thật thì ưu tiên **Bản đồ chiến thuật / sơ đồ địa hình / cảnh núi rừng biểu tượng**; nếu cần `image_prompt` dự phòng (`0 tín dụng` từ Google Flow) thì viết bối cảnh/địa hình/hiện vật đúng niên đại tập phim theo chất **Dark Hybrid Archival V5.7 (`restored_doc_still`)** (nền charcoal tẩy bạc tối + ánh sáng chọn lọc, không gắn cứng 1954 cho chủ đề khác) — **CẤM** minh họa 3D/AI bóng loáng và **CẤM** AI bịa hành động của nhân vật lịch sử thật.
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
  - Vẫn BẮT BUỘC viết đủ `image_prompt` theo đúng hệ mỹ thuật & `frame_preset` trong `storyboard.json` cho toàn bộ cảnh `static_image` để nếu cảnh nào chưa có ảnh thật thì `flow_bot.py` tự gen bù (`0 tín dụng` qua Nano Banana Pro), còn tấm nào đã có sẵn trong `assets/{id:03d}.jpg` thì bot tự động `SKIP_EXISTING_ASSET`.
- **Quy tắc Prompt chống cảm giác "quá AI / bóng nhựa" (45 – 65 từ tiếng Anh):**
  - Đưa góc máy + chủ thể + hành động lên ĐẦU CÂU. **Hạn chế tối đa vẽ cận mặt người thật bằng AI**; ưu tiên góc rộng toàn cảnh, góc từ sau vai (`over-the-shoulder`), bóng ngược sáng (`silhouetted figure`) hoặc đặc tả chi tiết (bàn tay, bản đồ, khí tài, hiện vật, địa hình).
  - **CẤM từ khóa gây bóng nhựa HDR, rực màu AI, tô vàng đạo cụ & ép đen trắng phẳng:** `8k, hyper-realistic, dramatic rim lighting, glossy, cinematic HDR, orange and teal, golden hour, Kodachrome, vivid green foliage, rosy skin tones, grayscale, sepia, pure flat black and white, bright white sky, rim-lighting on metal gear, yellowed paper border, vintage frame, fake scan frame`. Thay bằng House Look V5.8 đồng bộ toàn video (`heavy silver-halide bleach-bypass look`, `unified cold slate-charcoal near-monochrome shadows`, `heavy smoke-darkened overcast sky with cool desaturated slate-blue atmospheric mist`, `coarse tactile 35mm Tri-X analog film grain`).
  - **Khóa chặt trang phục & khí tài đúng thời kỳ của tập phim** (lấy từ bối cảnh/kịch bản từng dự án, không áp rập khuôn 1954 cho giai đoạn khác; tránh mũ sắt/bối cảnh sai thời kỳ, chữ rác AI). Với cảnh cận/bán thân hiếm hoi giữ `closed mouth`.
  - Luôn chốt đuôi đúng cú pháp `frame_preset` + `no text, no letters, no watermark`.
  - Tránh ghép tên riêng nhân vật có thật với từ `photograph` gây chặn policy Google Flow (ưu tiên góc sau vai, góc nghiêng bên bản đồ, hiện vật, địa hình).

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
- *(Tùy chọn V5.4 cho YouTube Series)*: cho phép gắn thêm `"chapter_title"` (chuỗi, cấp root JSON — đặt tên chương ngắn cho Thẻ chương & mục lục YouTube Chapters) và `"is_cta": true` (boolean ở cảnh cuối mời xem tập sau/theo dõi kênh — tab YouTube 16:9 tự nhận diện để gợi ý cắt CTA ở các tập giữa). `validate_storyboard.py` chấp nhận các trường này không cảnh báo.
- **ĐỒNG BỘ TUYỆT ĐỐI:** `beats[i].text === scenes[i].speech` từng ký tự; `"full_script_for_elevenlabs"` là chuỗi ghép toàn bộ `scenes[i].speech` cách nhau đúng 1 dấu cách.
- Nếu có `automation/scripts/validate_storyboard.py`, BẮT BUỘC chạy lệnh kiểm tra sau khi xuất JSON và tự sửa đến khi PASS 100%.

## 6. Quy chuẩn `Caption.md` cho TikTok
- Mỗi tập trong `story_mode/projects/<Ten_Series>/Caption.md` dùng đúng mẫu:
  `Phần {X} | {Tên Series ngắn}: {Hook/Tiêu đề tập hấp dẫn} #LearnOnTikTok #hoccungtiktok #dongluc #viral #xuhuong #lichsuVietNam #lichsu #tuhaodantoc #xuhuongtiktok #trending #tiktok #{hashtag_su_kien}`

## 7. Quy trình Dual-Delivery V5.4 — Sản xuất Song song YouTube 16:9 (`assets_16x9/`)
- **BẤT BIẾN AN TOÀN:** Tuyệt đối KHÔNG sửa đổi hay ghi đè `storyboard.json`, `assets/full_audio.mp3`, `assets/`, hay `raw_images/` vốn dùng cho TikTok/Shorts (`9:16` / `4:5` / `1:1`).
- **Thư mục song song:** Luồng YouTube 16:9 xuất riêng ảnh gốc vào `raw_images_16x9/` và tài nguyên thành phẩm `1920x1080` vào `assets_16x9/`, lưu trạng thái riêng tại `pipeline_state_16x9.json`.
- **Nâng cấp tư liệu thật bản ngang:** Chạy `fetch_archive_media.py --mode commit-image ... --youtube-16x9` (hoặc `--assets-dir ... --target-size 1920x1080`) để đưa ảnh thật `1920x1080` vào `assets_16x9/` trước khi chạy Flow, giúp bảo vệ 100% ảnh thật qua `SKIP_EXISTING_ASSET` và ghi thêm `asset_file_16x9` vào `archive_sources.json` mà giữ nguyên entry `assets/`.
- **Sinh bù AI tỉ lệ 16:9 trên Google Flow:** Chạy `run_episode.py <Tap_XX> --phase all --youtube-16x9` (hoặc `flow_bot.py <Tap_XX>\storyboard.json --youtube-16x9 --mode images|videos`):
  - Tự ép `frame_preset="landscape_16_9"` (nút `"16:9"` trên Google Flow).
  - Tạm thời chuyển chỉ thị khung dọc trên prompt gửi đi sang bố cục ngang widescreen (`adapt_prompt_for_frame_preset`) **trên bộ nhớ** mà không sửa `storyboard.json` trên đĩa.
- **Biên tập gộp toàn Series (`core/series_compiler.py` & Tab `📺 YouTube 16:9`):** Tự động ưu tiên `.mp4`/`.jpg` trong `assets_16x9/` (khi bật `prefer_assets_16x9`), tái sử dụng `assets/full_audio.mp3`, chỉ lùi về `assets/` nền mờ ở các cảnh chưa có bản `16:9`, và ghi rõ tỉ lệ `native 16:9` vs `fallback assets/` trong `compliance_report.json`.
