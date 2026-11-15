"""[KẾ HOẠCH - Phase 2] Schema dữ liệu chuẩn.

Hiện tại `Segment` vẫn nằm trong core/subtitles.py.
Sẽ chuyển sang đây và thêm:
    WordTiming(word: str, start: float, end: float)
để mọi nguồn mốc thời gian từng từ (Edge-TTS WordBoundary, ElevenLabs alignment,
faster-whisper) đều quy về cùng một kiểu trước khi sinh .ass.
"""
