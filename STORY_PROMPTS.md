# 🎬 DUBSTUDIO STORY MODE — Ý TƯỞNG & HỆ THỐNG PROMPT TIKTOK (LỊCH SỬ AI)

Tài liệu quy chuẩn sản xuất video dọc **9:16 (2–3 phút)** phong cách hoạt hình 2D người que (*OverSimplified / Genius History vibe*) tích hợp trực tiếp với **Kaggle GPU** và **DubStudio Phase 3**.

---

## 1. Thông số kỹ thuật mục tiêu (TikTok 2–3 phút)

- **Khung hình:** `1080x1920` (9:16), `30fps`, H.264 `-crf 24`, âm thanh AAC `128kbps` (dung lượng ~12–18MB).
- **Độ dài kịch bản:** `420 – 550 từ` tiếng Việt (tốc độ đọc ~1.15x–1.2x sẽ rơi vào khoảng 2 phút 15 giây – 2 phút 45 giây).
- **Mật độ hình ảnh:** `25 – 35 cảnh` (trung bình 4–5 giây đổi hình một lần để tránh người xem lướt qua).
- **Phong cách hình ảnh khóa cứng (Style Suffix):**
  > `2D flat vector cartoon illustration, OverSimplified style, cute round white stick-figure characters with simple expressive faces, bold dark outlines, solid flat colors, clean minimalist background, vertical 9:16 composition, no text, no watermark`

---

## 2. Danh sách 8 Tập mở màn (Kịch tính, dễ viral)

| Tập | Tiêu đề gợi tò mò | Hook 3 giây đầu | Cao trào chính |
| :--- | :--- | :--- | :--- |
| **01** | **Người đàn ông tạo ra AI bị chính đất nước mình hủy hoại** | "Năm 1950, một thiên tài đặt ra câu hỏi khiến cả thế giới run sợ: Máy móc có thể suy nghĩ không?" | Alan Turing giải mã Enigma, tạo phép thử Turing và bi kịch cuối đời. |
| **02** | **Mùa đông AI: Cú lừa thế kỷ khiến ngành AI suýt chết yểu** | "Bạn có biết AI từng bị cả thế giới chửi là trò lừa đảo và cắt sạch tiền tài trợ tận 2 lần?" | Lời hứa quá đà thập niên 70, báo cáo Lighthill và sự hồi sinh nhờ mạng nơ-ron. |
| **03** | **Trận cờ khiến nhân loại lần đầu mất ngôi vương (1997)** | "Nhà vô địch cờ vua vĩ đại nhất lịch sử đã nổi điên, tố cáo chiếc máy tính gian lận ngay trên bàn đấu." | Garry Kasparov đối đầu IBM Deep Blue — một lỗi bug kỳ lạ khiến ông hoảng loạn. |
| **04** | **Nước cờ 37: Khoảnh khắc AI biết 'sáng tạo' hơn con người** | "Các kỳ thủ chuyên nghiệp cười nhạo nước đi này là sai lầm ngớ ngẩn... cho đến 50 nước sau." | AlphaGo hạ gục Lee Sedol (2016) bằng nước đi không sách giáo khoa nào từng dạy. |
| **05** | **Bài báo 8 người viết làm thay đổi cả thế giới (2017)** | "Một nhóm kỹ sư Google viết xong bài báo này rồi bỏ đi hết... không ngờ nó đẻ ra ChatGPT." | Sự ra đời của kiến trúc Transformer (*Attention Is All You Need*) và cú trượt chân của Google. |
| **06** | **5 ngày đảo chính chấn động nhất lịch sử công nghệ** | "Thứ Sáu bị đuổi việc qua Google Meet, thứ Tư tuần sau quay lại làm chủ cả công ty." | Drama hội đồng quản trị OpenAI sa thải Sam Altman (11/2023) và cuộc phản công của Microsoft. |
| **07** | **Năm 2024: Khi AI bất ngờ ẵm trọn 2 giải Nobel** | "Các nhà khoa học truyền thống sốc toàn tập khi hai giải Nobel danh giá nhất lại rơi vào tay... dân làm AI." | Geoffrey Hinton (Nobel Vật lý) và Demis Hassabis / AlphaFold (Nobel Hóa học) năm 2024. |
| **08** | **Cú sốc mã nguồn mở làm bốc hơi hàng trăm tỷ đô (2025)** | "Các ông lớn Mỹ đốt hàng tỷ đô mua chip, bỗng một phòng lab ít tên tuổi tung mô hình miễn phí ngang cơ." | Cuộc đua mã nguồn mở bùng nổ (DeepSeek, Qwen, Llama) phá vỡ thế độc quyền AI. |

---

## 3. Hệ thống 3 Prompt chuẩn hóa (Dùng với Gemini API)

### Prompt 1: Lên thêm ý tưởng chủ đề mới
```text
Bạn là biên tập viên nội dung TikTok chuyên mảng Lịch sử Công nghệ & AI (phong cách kể chuyện kịch tính, dễ hiểu như kênh OverSimplified và Genius History).

Hãy đề xuất 10 ý tưởng video TikTok dài 2-3 phút về lịch sử AI, các bước ngoặt công nghệ, hoặc drama giữa các phòng lab/nhà khoa học AI.

Yêu cầu cho mỗi ý tưởng:
1. Tiêu đề: Ngắn gọn, đánh vào sự tò mò hoặc mâu thuẫn (không viết kiểu sách giáo khoa).
2. Hook 3 giây đầu: Một câu mở đầu gây sốc hoặc đặt câu hỏi khiến người xem phải dừng lại.
3. 3 mốc diễn biến chính: Khởi nguồn -> Xung đột/Cao trào -> Cú twist hoặc Di sản để lại.
4. Điểm nhấn hình ảnh: Gợi ý 1 hình ảnh hài hước hoặc ẩn dụ dễ vẽ bằng người que 2D.
Prompt 2: Viết kịch bản kể chuyện TikTok (2–3 phút)
Plaintext
Bạn là biên kịch TikTok chuyên nghiệp, phong cách kể chuyện cuốn hút, hài hước nhẹ, nhịp nhanh, dễ hiểu cho cả người không chuyên IT.

Hãy viết kịch bản lồng tiếng dài từ 450 đến 530 từ tiếng Việt (tương đương 2 phút 15 giây đến 2 phút 45 giây đọc) cho chủ đề:
[DÁN CHỦ ĐỀ VÀO ĐÂY]

Cấu trúc bắt buộc (giữ nhịp giữ chân người xem):
1. HOOK (0s - 10s): Mở đầu thẳng vào cao trào hoặc một sự thật gây sốc. Không chào hỏi "Xin chào các bạn".
2. BỐI CẢNH (10s - 40s): Giải thích nhanh nhân vật/tình huống trước khi sự kiện xảy ra.
3. CAO TRÀO & MID-HOOK (40s - 105s): Đẩy kịch tính lên cao. Chèn ít nhất 2 câu chuyển nhịp giữ chân kiểu "Nhưng khoảnh khắc điên rồ nhất vẫn chưa tới..." hoặc "Không ai ngờ rằng...".
4. CÚ TWIST & KẾT (105s - 150s): Giải quyết vấn đề, rút ra tác động tới hiện tại (2026) và kết bằng 1 câu hỏi ngắn kích thích bình luận.

Quy tắc văn phong cho TTS tiếng Việt:
- Câu ngắn, gọn, đúng văn nói tự nhiên (mỗi câu tối đa 15-20 từ để đọc không bị hụt hơi).
- Viết rõ phiên âm hoặc cách đọc nếu có từ tiếng Anh khó, tránh viết tắt khó đọc.
- Chỉ xuất phần lời thoại chia thành từng đoạn ngắn, không giải thích thừa.
Prompt 3: Chuyển kịch bản thành storyboard.json (Cho Kaggle + DubStudio)
Plaintext
Bạn là đạo diễn hình ảnh hoạt hình 2D người que (phong cách OverSimplified).
Hãy chia kịch bản tiếng Việt dưới đây thành danh sách các phân cảnh (khoảng 25-35 cảnh, mỗi cảnh tương ứng 1-2 câu thoại ngắn kéo dài 4-6 giây) và xuất ra DUY NHẤT một khối JSON hợp lệ, không kèm văn bản giải thích bên ngoài.

Quy tắc tạo `image_prompt` (bắt buộc viết bằng tiếng Anh):
- Luôn giữ nhất quán nhân vật: người que đầu tròn trắng, viền đen đậm, nét mặt biểu cảm rõ (sốc, cười đểu, đổ mồ hôi, tức giận), mặc đồ/phụ kiện đặc trưng để phân biệt (ví dụ: áo vest cổ điển cho Alan Turing, kính tròn, áo hoodie, logo trên áo).
- Đảo góc máy liên tục giữa các cảnh liền kề: close-up (cận mặt biểu cảm), medium shot (2 nhân vật đối thoại), wide shot (toàn cảnh phòng lab/sân khấu), diagram/map shot (biểu đồ tăng trưởng, sơ đồ mạng nơ-ron, mũi tên chiến thuật).
- KHÔNG đưa chữ (text) vào ảnh để tránh lỗi chính tả khi AI vẽ.
- KHÔNG cần lặp lại style suffix ở mỗi prompt (hệ thống Kaggle sẽ tự nối vào sau).

Định dạng JSON bắt buộc:
{
  "title": "Tên video",
  "aspect_ratio": "9:16",
  "style_suffix": "2D flat vector cartoon illustration, OverSimplified style, cute round white stick-figure characters with simple expressive faces, bold dark outlines, solid flat colors, clean minimalist background, vertical 9:16 composition, no text, no watermark",
  "scenes": [
    {
      "id": 1,
      "speech": "Câu thoại tiếng Việt chính xác của cảnh này.",
      "image_prompt": "Close-up of a round white stick-figure scientist in a 1950s tweed suit sweating nervously while looking at a giant glowing vintage computer machine, dramatic dark blue background",
      "motion": "zoom_in",
      "sfx": "boom",
      "on_screen_text": "NĂM 1950"
    }
  ]
}

Giá trị hợp lệ cho `motion`: "zoom_in", "zoom_out", "pan_left", "pan_right", "static".
Giá trị hợp lệ cho `sfx`: "none", "whoosh", "boom", "pop", "typing", "alarm".

Kịch bản đầu vào:
[DÁN KỊCH BẢN TỪ PROMPT 2 VÀO ĐÂY]
Mày lưu file này vào repo làm tài liệu chuẩn luôn. Khi nào có file zip hoặc muốn test thử quy trình ngay, mình có thể chạy nháp **Tập 01 (Alan Turing)** hoặc **Tập 05 (Bài báo Transformer)** để xem `storyboard.json` ra có đẹp không trước khi code Phase 3 cho DubStudio.

<FollowUp label="Muốn tao viết thử trọn bộ kịch bản và storyboard.json cho Tập 1 không?" query="Hãy dùng Prompt 2 và Prompt 3 để viết thử kịch bản tiếng Việt và file storyboard.json hoàn chỉnh cho Tập 1 về Alan Turing."/>