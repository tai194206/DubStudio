# DubStudio · Kaggle Pipeline

Lấy **kịch bản** từ video bằng GPU miễn phí của Kaggle, rồi chuẩn bị bản dịch **đọc kịp khung thời gian** để đưa vào DubStudio.

```
video ──► [Notebook 01] ──► script.txt / transcript.srt / transcript.json / qc_report.md
                                   │
                                   ▼
          [Notebook 02] ──► script_vi.txt / fit_report.md ──► DubStudio (📂 Mở... → ② Lồng tiếng)
```

> ⚠️ **Chỉ dùng với video bạn sở hữu hoặc được phép sử dụng.** Gói này không có và sẽ không có tính năng né nhận diện bản quyền.

---

## 1. Có gì trong gói

```text
dubstudio_kaggle/
├── dubkaggle.py                  # Lõi xử lý (đã test). Notebook nhúng nguyên văn file này
├── notebooks/
│   ├── 01_transcribe.ipynb       # Video -> kịch bản + báo cáo QC
│   └── 02_translate_fit.ipynb    # Dịch Gemini + kiểm tra tốc độ đọc + rút gọn câu quá nhanh
├── tools/
│   ├── build_notebooks.py        # Sinh lại notebook từ dubkaggle.py
│   └── make_samples.py           # Sinh file mẫu trong samples/
├── tests/                        # 51 test, chạy offline
├── samples/                      # Mẫu định dạng đầu ra (dữ liệu minh họa tự viết)
├── requirements-dev.txt
└── README.md
```

**Quy ước:** muốn sửa logic thì sửa `dubkaggle.py` rồi chạy `python tools/build_notebooks.py`. Test `test_committed_notebooks_are_up_to_date`
sẽ báo lỗi nếu notebook chưa được dựng lại, để code chạy trên Kaggle luôn là code đã test.

---

## 2. Chạy trên Kaggle

### Notebook 01: Video → Kịch bản
1. Vào Kaggle → **Create → New Notebook** → **File → Import Notebook** → chọn `notebooks/01_transcribe.ipynb`.
2. **Settings:** Accelerator = **GPU (T4/P100)**; **Internet = On** (tải model lần đầu, có thể cần xác minh số điện thoại).
3. **Add Input → Upload** video của bạn thành một dataset. Notebook tự tìm trong `/kaggle/input`.
4. Sửa ô **Cấu hình** (`language`, `hotwords`, ...) rồi **Run All**.
5. Tải `dubstudio_output.zip` ở tab **Output**.

### Notebook 02: Dịch + kiểm tra tốc độ đọc
1. Thêm key Gemini: **Add-ons → Secrets**, tên `GEMINI_API_KEY`. **Không dán key vào notebook.**
2. Đưa `transcript.json` vào (chạy Notebook 02 ngay sau Notebook 01 trong cùng phiên, hoặc upload `transcript.json` thành dataset).
3. Sửa ô cấu hình: `glossary` (thuật ngữ), `pronoun_rules` (xưng hô), `speed_cap`, rồi **Run All**.
4. Tải `dubstudio_translate_vi.zip`.

### Đưa vào DubStudio
Tab **Dub Studio → 📂 Mở...** chọn `script_vi.txt` → đọc lại, sửa tay nếu cần → **② Lồng tiếng & xuất video**.
(`script.txt` của Notebook 01 là **ngôn ngữ gốc**, phải dịch trước. Dịch bằng Notebook 02 hoặc tab **Dịch kịch bản** của DubStudio.)

---

## 3. Cấu hình chính

### `Config` (Notebook 01)
| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `language` | `"zh"` | Mã ngôn ngữ của video, hoặc `"auto"`. Chỉ rõ thường chính xác hơn `auto` |
| `model_size` | `"large-v3"` | `large-v3` chính xác nhất; `medium` nhanh/nhẹ hơn |
| `hotwords` | `""` | **Tên riêng cần nhận đúng**, cách nhau dấu phẩy (nhân vật, địa danh) |
| `separate_vocals` | `False` | Tách giọng khỏi nhạc nền bằng Demucs. Bật khi nhạc nền to (xem mục Trạng thái kiểm chứng) |
| `recheck_gaps` | `True` | Quét lại khoảng trống dài với VAD tắt để vớt lời bị bỏ sót |
| `gap_recheck_min` | `4.0` | Khoảng trống dài từ mức này (giây) mới được quét lại |
| `max_seg_seconds` | `8.0` | Câu dài hơn mức này sẽ được tách. Câu ngắn dễ khớp thời gian hơn |
| `min_seg_seconds` / `merge_gap` | `0.6` / `0.35` | Câu ngắn hơn mức này và cách nhau ít hơn `merge_gap` giây sẽ được gộp |
| `vad_filter` | `True` | Bỏ đoạn không có tiếng nói. Quá khắt khe thì bật `recheck_gaps` để vớt lại |
| `condition_on_previous_text` | `False` | Tắt để giảm lặp vòng khi có nhạc nền |

### `TranslateConfig` (Notebook 02)
| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `glossary` | `""` | `Tên gốc=Tên dịch`, cách nhau dấu phẩy |
| `pronoun_rules` | `""` | Quy tắc xưng hô, ví dụ `Nobita gọi Doraemon là 'Doraemon'; Doraemon xưng 'tớ'` |
| `speed_cap` | `1.25` | Tốc độ đọc tối đa chấp nhận được. Câu cần nhanh hơn sẽ bị rút gọn |
| `cps` | `0` | Ký tự/giây của giọng đọc. `0` = ước lượng mặc định. **Nên đo thật** bằng ô hiệu chỉnh |
| `shorten_rounds` | `2` | Số vòng rút gọn tự động |
| `model` / `fallback_models` | xem code | Model Gemini. Tên đổi liên tục nên notebook có ô liệt kê model thật từ API |

---

## 4. Định dạng đầu ra

| File | Nội dung |
|---|---|
| `script.txt` | `[00:00:01.200 -> 00:00:03.400] nội dung`: DubStudio đọc trực tiếp |
| `transcript.srt` | SRT chuẩn |
| `transcript.json` | Schema `dubstudio.transcript/1`: từng câu kèm `words` (mốc từng từ + `prob`), `flags`, `avg_logprob`, `no_speech_prob` |
| `qc_report.md/.json` | Báo cáo kiểm tra chất lượng |
| `fit_report.md/.json` | Báo cáo tốc độ đọc sau khi dịch |

Xem ví dụ trong `samples/`. `transcript.json` không chứa đường dẫn máy hay key.

---

## 5. Cách hoạt động

### Giai đoạn 1 (Notebook 01)
1. **Tách âm thanh** mono 16 kHz, chuẩn hóa âm lượng bằng `loudnorm` để Whisper nghe rõ đoạn nói nhỏ.
2. *(Tùy chọn)* tách giọng khỏi nhạc nền bằng Demucs; thất bại thì dùng audio gốc.
3. **Nhận diện** bằng `faster-whisper` với mốc từng từ, VAD, `hotwords`. Tự hạ cấp GPU float16 → GPU int8_float16 → CPU int8 khi gặp lỗi CUDA.
4. **Lọc câu Whisper "bịa":** chỉ bỏ khi *vừa giống cụm Whisper hay bịa* (vd "字幕由…", "Thanks for watching") *vừa* có độ tin cậy thấp,
   hoặc khi `no_speech` rất cao + `logprob` thấp, hoặc văn bản lặp vòng. Câu thật chứa cụm đó với độ tin cậy cao được **giữ lại**.
5. **Bỏ câu lặp** liên tiếp quá 2 lần; dọn mốc thời gian (sắp xếp, hết chồng lấn, ép trong độ dài video).
6. **Gộp câu quá ngắn** và **tách câu quá dài** tại chỗ ngắt hợp lý (ưu tiên dấu câu, khoảng nghỉ dài, gần giữa câu).
7. **Quét lại khoảng trống dài** với VAD tắt, lọc nghiêm hơn; câu tìm lại được gắn cờ `recovered`.
8. **Gắn cờ QC** và xuất file.

| Cờ | Nghĩa |
|---|---|
| `low_conf` | Độ tin cậy thấp, nên nghe lại |
| `no_speech` | Có thể không phải lời nói (nhạc, tiếng động) |
| `repetitive` | Văn bản lặp, nghi lỗi nhận diện |
| `fast_speech` | Người nói rất nhanh, dịch sang tiếng khác dễ bị quá ngắn khung |
| `suspect_phrase` | Giống cụm Whisper hay bịa (nhưng độ tin cậy cao nên được giữ) |
| `recovered` | Câu tìm lại ở lần quét khoảng trống |

### Giai đoạn 2 (Notebook 02)
1. **Dịch theo lô** (40 câu/lô) có ngữ cảnh 4 câu trước, thuật ngữ, xưng hô; mỗi câu có `max_chars` tính từ **khung thời gian được mượn khoảng lặng phía sau**
   (giống cách DubStudio tính). Câu thiếu được dịch lại riêng; thất bại thì giữ bản gốc và gắn cờ `untranslated`.
2. **Ước lượng tốc độ đọc:** `tốc độ cần = (số ký tự / cps) / khung thời gian`. Phân loại `ok` (≤ 1.0x), `tight` (≤ `speed_cap`), `too_fast` (> `speed_cap`).
3. **Rút gọn tự động** các câu `too_fast` bằng Gemini; chỉ nhận bản **ngắn hơn và không rỗng**; tối đa `shorten_rounds` vòng.
4. **Gợi ý làm chậm video chung** chỉ khi mức cần thiết ≤ 1.2x. Nếu vài câu đơn lẻ đòi làm chậm nhiều hơn, báo cáo nói thẳng là **không khuyến nghị**
   và khuyên rút gọn/sửa tay các câu đó.

---

## 6. Về chuyện giọng đọc "bị bỏ qua vì quá nhanh"

Nguyên nhân gốc là **bản dịch dài hơn khoảng thời gian của câu gốc**. Thứ tự xử lý nên dùng:

1. **Rút gọn lời** (làm ở Notebook 02): giữ được giọng tự nhiên nhất.
2. **Tăng tốc giọng vừa phải** (≤ ~1.25–1.3x): DubStudio đã có `max_speed`.
3. **Sửa tay** các câu còn lại trong `fit_report.md`.
4. **Làm chậm video:** chỉ hợp lý khi mức chênh nhỏ. Làm chậm cả video lên nhiều sẽ kéo chuyển động; báo cáo sẽ nói rõ khi đó không đáng.

> Việc **làm chậm video theo từng đoạn** (chỉ những câu quá nhanh) và các tùy chọn tốc độ ở phía DubStudio **chưa được làm** trong gói này.
> Gói này chỉ *phát hiện* và *rút gọn lời*.

Còn **lỗi `No audio was received` của Edge-TTS** (câu bị thay bằng im lặng) là chuyện khác: do máy chủ TTS giới hạn tốc độ/lỗi mạng,
cần xử lý ở `core/tts.py` của DubStudio (giảm số luồng song song, thử lại có backoff), không nằm trong gói này.

---

## 7. Trạng thái kiểm chứng (trung thực)

| Hạng mục | Trạng thái |
|---|---|
| Logic hậu xử lý, QC, quét khoảng trống, dịch, rút gọn, xuất file | ✅ 51 test offline; đã làm kiểm tra đột biến (cố ý phá code để chắc test bắt được) |
| Các bước FFmpeg (tách audio, cắt đoạn, đo thời lượng) | ✅ Chạy thật trên video mẫu do FFmpeg tạo |
| Từng ô của 2 notebook | ✅ Chạy thật từng ô, nối Notebook 01 → 02, với `faster_whisper` và `google.genai` **giả lập** |
| Tên tham số `faster-whisper` (`hotwords`, `vad_parameters`, `word_timestamps`...) | ✅ Đối chiếu với `faster-whisper 1.2.1` bằng cách đọc chữ ký hàm |
| File đầu ra đọc được bằng parser của DubStudio | ✅ Đã thử với parser trong bản repo tại thời điểm làm; đọc đúng 6/6 câu cả `.txt` và `.srt` |
| **Chạy thật** `faster-whisper` trên GPU Kaggle | ❌ **Chưa** (cũng chưa đo tốc độ/độ chính xác thực tế) |
| Hạ cấp GPU → CPU khi lỗi CUDA | ⚠️ Chỉ test bằng lỗi giả lập; lỗi CUDA nặng có thể làm sập cả tiến trình, không bắt được |
| Demucs (`separate_vocals`) | ❌ Chưa chạy thật |
| Gọi Gemini thật (dịch, rút gọn, liệt kê model) | ❌ Chưa chạy thật |
| Ô hiệu chỉnh Edge-TTS (`CALIBRATE`) | ❌ Chưa chạy thật (cần Internet) |
| Kaggle Secrets / đường dẫn `/kaggle/input` | ❌ Chưa thử trên Kaggle thật |
| Chất lượng dịch, xưng hô, ngưỡng lọc câu bịa | ❌ Cần đánh giá bằng mắt trên video thật; các ngưỡng là giá trị khởi đầu hợp lý, chưa được chỉnh theo dữ liệu thật |

**Lần chạy đầu tiên trên Kaggle:** hãy thử với một clip ngắn (1–2 phút) trước, rồi gửi lại log nếu có lỗi.

---

## 8. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| `Could not load library libcudnn...` | Notebook tự hạ cấp xuống cấu hình khác. Nếu tiến trình bị dừng hẳn: **Restart** rồi đặt `device="cpu"` (chậm) hoặc `model_size="medium"` |
| Hết VRAM | `model_size="medium"` hoặc `compute_type="int8_float16"` |
| Không tải được model | Bật **Internet: On** trong Settings |
| `Không thấy video` | Add Input chưa đúng, hoặc điền `VIDEO` thủ công |
| Nhận diện sai ngôn ngữ | Đặt `language` cụ thể thay vì `"auto"` |
| Sai tên nhân vật | Điền `hotwords` ở Notebook 01 và `glossary` ở Notebook 02 |
| Nhiều câu "bịa"/lặp ở đoạn nhạc | Bật `separate_vocals`; xem cờ `no_speech`, `repetitive` trong `qc_report.md` |
| Thiếu lời ở đoạn có thoại | Xem mục *Khoảng trống dài* trong báo cáo QC; giảm `gap_recheck_min`; thử `vad_filter=False` |
| Gemini `403` / `404` | Model không dùng được với key của bạn; notebook tự thử model khác. Xem danh sách model ở ô liệt kê |
| Gemini `429` | Hết hạn mức; notebook tự chờ và thử lại, nếu vẫn lỗi thì chạy lại sau |
| Output tiếng Trung phồn thể/giản thể lẫn lộn | Whisper có thể trả cả hai; Gemini xử lý được khi dịch |

---

## 9. Phát triển

```bash
pip install -r requirements-dev.txt     # cần FFmpeg cài sẵn trên máy
pytest -q                               # 51 test, không cần GPU/API key/internet
python tools/build_notebooks.py         # dựng lại notebook sau khi sửa dubkaggle.py
python tools/make_samples.py            # dựng lại samples/
```

Nếu đưa gói này vào repo DubStudio, nên để thành thư mục riêng (ví dụ `kaggle/`) và chạy `pytest` **bên trong thư mục đó** để không lẫn với `tests/` của DubStudio.

## 10. Lưu ý

- Chỉ xử lý nội dung bạn có quyền sử dụng. Dịch và lồng tiếng lại video của người khác để đăng kiếm tiền có thể vi phạm bản quyền và chính sách nền tảng.
- Edge-TTS (dùng ở ô hiệu chỉnh) là dịch vụ không chính thức, chỉ phù hợp thử nghiệm.
- `GEMINI_API_KEY` chỉ lấy từ Kaggle Secrets hoặc biến môi trường; notebook không lưu key vào file đầu ra.
- Chưa chọn giấy phép cho mã nguồn.
