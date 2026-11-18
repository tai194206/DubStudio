# Hướng Dẫn & Bộ Prompt Quy Trình Sản Xuất Video Lịch Sử (storyboard_v2)

---

## 1. Định nghĩa Schema storyboard_v2.json

Cấu trúc này hợp nhất kết quả của Prompt 2 và Prompt 3, đồng thời hỗ trợ cơ chế Hybrid (Kết hợp): chỉ dùng video Google Flow cho 3–5 cảnh đắt giá nhất (`asset_type: "flow_video"`), các cảnh còn lại dùng ảnh tĩnh Kaggle chạy hiệu ứng Ken Burns (`asset_type: "static_image"`) để tiết kiệm credit và thời gian chờ.

```json
{
  "version": "2.0",
  "title": "Hành trình tìm đường cứu nước - Bến Nhà Rồng 1911",
  "aspect_ratio": "9:16",
  "total_words": 285,
  "style_prefix": "2D flat vector illustration, OverSimplified historical style, bold black outlines, solid flat colors, vertical 9:16",
  "character_bible": "Young Vietnamese man in 1911, slim build, short neat black hair, wearing a simple white traditional button shirt and dark trousers",
  "full_script_for_elevenlabs": "Ngày mồng 5 tháng 6 năm 1911, tại bến cảng Nhà Rồng...",
  "scenes": [
    {
      "id": 1,
      "speech": "Ngày mồng 5 tháng 6 năm 1911, tại bến cảng Nhà Rồng, một quyết định lịch sử đã được đưa ra.",
      "source_note": "Tài liệu Bảo tàng Hồ Chí Minh - Sự kiện tàu Amiral Latouche-Tréville.",
      "asset_type": "flow_video",
      "image_prompt": "Wide low-angle shot, 2D flat vector illustration, OverSimplified historical style, young Vietnamese man in 1911 wearing a simple white shirt and dark trousers standing at Saigon harbor looking at a massive French steamship, warm morning sunlight, vertical 9:16",
      "motion_prompt": "Slow camera push-in toward the young man, gentle harbor water ripples, steam rising from the ship chimney.",
      "ken_burns": "zoom_in",
      "sfx": "ship_horn",
      "on_screen_text": "5/6/1911 - BẾN NHÀ RỒNG"
    }
  ]
}
```

---

## 2. Bộ 3 Prompt Đã Hiệu Chỉnh (Chuẩn hóa lịch sử, Độ dài & JSON)

### 🔹 PROMPT 1: Tra cứu & Kiểm chứng Sự kiện Lịch sử (Có trích nguồn)

```plaintext
Bạn là Chuyên gia Nghiên cứu Lịch sử Việt Nam và Thế giới.
Nhiệm vụ của bạn là đề xuất 5 sự kiện lịch sử có thật, có giá trị tiêu biểu xoay quanh chủ đề tôi yêu cầu ở cuối bài để làm video tài liệu ngắn.

QUY TẮC KIỂM CHỨNG BẮT BUỘC (ĐẶC BIỆT VỚI NHÂN VẬT LỊCH SỬ NHƯ BÁC HỒ):
1. Tuyệt đối tôn trọng sự thật lịch sử. Không tự bịa đặt tình tiết kịch tính hóa, không tự gán ghép lời nói trực tiếp cho nhân vật nếu không có văn bản/tài liệu lịch sử chính thống ghi nhận.
2. Với mỗi sự kiện, phải ghi rõ nguồn đối chiếu (Sách giáo khoa Lịch sử, văn kiện, hồi ký hoặc tài liệu bảo tàng chính thống).
3. Nếu có chi tiết nào thuộc dạng giai thoại hoặc chưa đồng nhất giữa các nguồn, bắt buộc phải gắn nhãn [CẦN KIỂM CHỨNG LẠI].

ĐỊNH DẠNG ĐẦU RA CHO MỖI SỰ KIỆN:
- Tên sự kiện & Thời gian, địa điểm:
- Nguồn tài liệu đối chiếu:
- Tóm tắt diễn biến xác thực (Khởi đầu -> Diễn biến chính -> Ý nghĩa lịch sử):
- Gợi ý 3-5 cảnh đắt giá nhất nên làm video chuyển động (Flow) và các cảnh còn lại nên dùng ảnh tĩnh:

---
CHỦ ĐỀ / NHÂN VẬT LỊCH SỬ CẦN TRA CỨU:
[ĐIỀN CHỦ ĐỀ VÀO ĐÂY]
```

---

### 🔹 PROMPT 2: Viết Kịch bản (250–350 từ) & Xuất Khung storyboard_v2.json

```plaintext
Bạn là Biên kịch Phim Tài liệu Lịch sử. Dựa vào sự kiện đã được kiểm chứng ở cuối bài, hãy viết kịch bản lồng tiếng cho video ngắn (tỉ lệ 9:16).

QUY TẮC BẮT BUỘC:
1. Độ dài chuẩn: Tổng số từ nằm trong khoảng 250 đến 350 từ (tương đương 90 - 120 giây đọc), chia thành 18 đến 24 cảnh (mỗi cảnh 1 câu từ 12 - 18 từ).
2. Độ chính xác: Bám sát tài liệu nguồn đã cung cấp. Không tự ý thêm lời thoại trực tiếp của nhân vật lịch sử nếu nguồn không ghi nhận. Không mô tả các hành động giả lập sai lệch lịch sử.
3. Phân bổ tài nguyên (Hybrid Mode): Đánh dấu trường "asset_type" là "flow_video" cho 4-5 cảnh quan trọng nhất (Cảnh mở đầu Hook và các cảnh Cao trào), các cảnh còn lại đặt là "static_image" (dùng ảnh tĩnh chạy Ken Burns).
4. Đầu ra gồm 2 phần:
   - PHẦN 1: Bản đọc liền mạch (Text thuần, không ký tự lạ, dùng để đưa vào API ElevenLabs /with-timestamps).
   - PHẦN 2: Khung JSON chuẩn theo schema storyboard_v2 (chưa cần điền chi tiết image_prompt và motion_prompt, để trống "" cho Bước 3 xử lý, nhưng phải điền đầy đủ id, speech, source_note, asset_type, ken_burns, sfx, on_screen_text).

---
SỰ KIỆN LỊCH SỬ ĐÃ KIỂM CHỨNG:
[DÁN SỰ KIỆN TỪ BƯỚC 1 VÀO ĐÂY]
```

---

### 🔹 PROMPT 3: Điền Image Prompt (40–60 từ) & Motion Prompt Ngắn (20–30 từ) vào JSON

```plaintext
Bạn là Đạo diễn Hình ảnh cho dự án video lịch sử 2D. Nhiệm vụ của bạn là nhận khung JSON từ Bước 2 ở cuối bài, bổ sung trường "character_bible", và điền đầy đủ "image_prompt" cùng "motion_prompt" cho từng cảnh, sau đó trả về file JSON hoàn chỉnh.

QUY TẮC VIẾT PROMPT NGẮN GỌN, HIỆU QUẢ:
1. "character_bible": Mô tả ngắn gọn (15-20 từ tiếng Anh) ngoại hình và trang phục lịch sử chuẩn xác của nhân vật chính để giữ đồng nhất.
2. "image_prompt" (Giới hạn 40 - 60 từ tiếng Anh):
   - Cấu trúc: [Góc máy: Wide shot / Medium shot / Close-up] + [Style: 2D flat vector illustration, OverSimplified historical style, bold black outlines, solid flat colors] + [Mô tả nhân vật từ character_bible + Hành động] + [Bối cảnh lịch sử xác thực & Ánh sáng, vertical 9:16].
   - Không yêu cầu chồng chéo quá nhiều lớp phức tạp gây rối hình.
3. "motion_prompt" (Giới hạn 20 - 30 từ tiếng Anh):
   - Chỉ viết đúng 3 yếu tố đơn giản: [1 chuyển động máy quay: Slow zoom-in / Gentle pan left / Static camera] + [1 cử động đơn giản của nhân vật] + [1 chuyển động phụ của môi trường như gió, mây, khói, nước].
   - TUYỆT ĐỐI BỎ các từ khóa rác không có tác dụng thực tế như "no morphing, 4k, 8k, hyper-realistic, stable geometry".
   - Với các cảnh có "asset_type": "static_image", vẫn viết motion_prompt dự phòng ngắn gọn.

YÊU CẦU ĐẦU RA:
Chỉ trả về duy nhất khối code JSON hoàn chỉnh (storyboard_v2.json) đã được điền đầy đủ mọi trường.

---
DƯỚI ĐÂY LÀ KHUNG JSON TỪ BƯỚC 2:
[DÁN KHỐI JSON TỪ BƯỚC 2 VÀO ĐÂY]
```
Ý tưởng bán tự động này đúng hướng. Giữ người duyệt ở khâu nguyên liệu (giọng, ảnh, clip) và để máy làm phần lặp lại là cách hợp lý. Nhưng bản phân tích bạn dán có một số chỗ sai hoặc quá lạc quan, nên mình không khuyên làm theo nguyên văn.

Chỗ đúng

Tách công đoạn: duyệt nguyên liệu bằng mắt, còn cắt, căn giờ và phụ đề để máy làm.
Ý tưởng dùng mốc thời gian để cắt clip khớp giọng đọc là đúng. Đây là điều tốn công nhất nếu làm tay.
Prompt 2 yêu cầu xuất hai phần (bản đọc liền mạch và bản tách cảnh) là hợp lý.
Chỗ cần sửa

Phụ đề nên lấy từ ElevenLabs, không cần Whisper.
Endpoint /with-timestamps của ElevenLabs trả về thời điểm từng ký tự. Bạn có thể cắt thành mốc từng câu và tô màu karaoke theo đúng giờ, chính xác hơn Whisper đoán lại từ âm thanh. Đây cũng là hướng mình đã đưa vào thiết kế DubStudio. Vì vậy bước Kaggle viết phụ đề không cần thiết, và Kaggle chỉ nên giữ cho việc vẽ ảnh nếu bạn muốn tiết kiệm credit.

2. Đừng viết lại công cụ dựng mới. Phần “tool Python cắt khớp video theo giọng” đã có trong thư mục story_mode_v1 mình làm trước đó: đọc storyboard, cắt hoặc lặp clip theo thời lượng cảnh, ghép âm thanh, xuất phụ đề. Điều cần làm là đưa clip Flow và mốc thời gian từ ElevenLabs vào đó.

3. Số từ kịch bản đang mâu thuẫn. Prompt 2 yêu cầu 220–260 từ (khoảng 60–75 giây), còn storyboard bên story_mode khuyến nghị 420–550 từ. Phải chọn một mục tiêu duy nhất, nếu không bước dựng sẽ luôn báo cảnh báo. Với TikTok, mình khuyên 250–350 từ (khoảng 90–120 giây, tùy tốc độ đọc).

4. Hiệu ứng “3 lớp chiều sâu” và lệnh motion dài là đánh giá quá cao. Mô hình video hiện nay không tuân theo ổn định các lệnh nhiều lớp. Prompt càng dài và càng nhiều yêu cầu, chuyển động càng dễ trôi, nhân vật càng dễ biến dạng. Nên giữ motion prompt ngắn: một chuyển động máy quay, một hành động của nhân vật, một chuyển động của môi trường. Các cụm như “no morphing, 4k” không có tác dụng đáng tin cậy.

5. Cần kiểm tra thông số Google Flow trước khi dựa vào. Số giây tối đa, tỉ lệ 9:16 và giới hạn độ dài prompt đều cần đối chiếu với giao diện hiện tại. Mình không xác nhận được các chi tiết này.

6. Thời gian xử lý được ước tính quá lạc quan. Faster-whisper trên CPU với model lớn có thể mất nhiều hơn 2–5 giây. Con số đó chỉ đúng với GPU và model nhỏ. Với phương án ElevenLabs timestamps, bạn không cần chạy Whisper nữa, nên vấn đề này không còn quan trọng.

7. Bác Hồ là nhân vật lịch sử có thật, nên cần thận trọng hơn bình thường. Yêu cầu AI “đề xuất sự kiện kịch tính” dễ sinh chi tiết sai hoặc câu nói được bịa ra. Với một nhân vật như vậy, mỗi sự kiện và mỗi câu thoại nên đối chiếu nguồn đáng tin (sách lịch sử, tài liệu chính thống), và không gán lời nói cụ thể cho nhân vật nếu không có nguồn. Đừng vẽ hình ảnh giả lập các cảnh chưa từng được ghi nhận mà không nói rõ là dựng lại.

Phương án đề xuất

BướcLàm gìCông cụ1Chọn sự kiện, kiểm tra từng mốc với nguồnPrompt 1 + tự kiểm tra2Viết kịch bản, xuất đồng thời bản đọc liền mạch và storyboard JSONPrompt 2 (sửa để xuất JSON)3Tạo giọng một lần, lấy alignment theo ký tựElevenLabs with-timestamps4Sinh image prompt và motion prompt ngắn cho từng cảnhPrompt 3 (sửa để xuất JSON)5Vẽ ảnh rẻ bằng SDXL (tùy chọn), chỉ làm video Flow cho 3–5 cảnh quan trọng nhấtKaggle hoặc Flow6Dựng phim, tạo phụ đề karaoke từ alignment, xuất MP4story_mode (local)

Nguyên tắc tiết kiệm thời gian nhất: để tốn công nhất vào bước 4–5 vì đó là chỗ mất nhiều thời gian nhất (mỗi cảnh video phải đợi generate, có thể tạo lại nhiều lần). Đưa phần còn lại vào storyboard JSON để không phải copy tay từng câu. Chỉ dùng video động cho cảnh hook và cao trào, các cảnh còn lại dùng ảnh tĩnh có chuyển động Ken Burns. Cách này vừa tiết kiệm credit Flow vừa giữ được cảm giác sống động ở những chỗ người xem chú ý nhất.

Điều chỉnh cho ba prompt

Prompt 1: thêm yêu cầu trích nguồn cho từng sự kiện và đánh dấu rõ những chi tiết không chắc chắn.
Prompt 2: đổi đầu ra thành JSON có các trường speech, image_prompt, motion_prompt, sfx, on_screen_text, khớp với định dạng storyboard. Bản đọc liền mạch vẫn giữ để có kịch bản hoàn chỉnh.
Prompt 3: tạo prompt cho AI gg flow tạo hình ảnh tạo hình ảnh(prompt 3) xong thì cho prompt motion(prompt 5) để tạo motion đúng sự kiện lịch sử cảnh . 2 prompt trên tạo từ kịch bản mà chúng ta phải tạo một prompt chính để ra promt 4 + 5
prompt4 : là prompt hình ảnh để bỏ lệnh vào gg flow để nó tạo hình ảnh đúng yêu cầu của mình bối cảnh đúng kịch bản
prompt 5 : là prompt motion khi đã có hình ảnh thì sẽc tạo chuyển động cần một prompt hoàn chỉnh và đúng bối cnahr và cso sự liên kết 

4+5 là tạo ra từ prompt 3 