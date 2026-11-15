"""Phong cách dịch mẫu và danh sách model Gemini dự phòng.
Tên model đổi thường xuyên: nút 'Làm mới' trong GUI luôn lấy danh sách thật từ API.
"""

STYLE_PRESETS = {
    "Tự nhiên": "Tự nhiên, dễ nghe, đúng văn nói",
    "Review Gen Z": "Năng động, hài hước, dùng ngôn ngữ mạng vừa phải, nhịp câu ngắn",
    "Kể chuyện": "Kể chuyện lôi cuốn, có cao trào, câu văn giàu hình ảnh, nhịp chậm rãi",
    "Anime / Tu tiên": "Văn phong cổ trang nhẹ, trang trọng, dùng từ Hán Việt vừa phải",
    "Giáo dục": "Rõ ràng, chính xác, lịch sự, không dùng tiếng lóng",
}

FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
