"""Các lỗi riêng của ứng dụng."""


class DubberError(Exception):
    """Lỗi chung, thông điệp đủ thân thiện để hiện cho người dùng."""


class MissingDependency(DubberError):
    """Thiếu thư viện tùy chọn (faster-whisper, demucs...)."""


class TTSError(DubberError):
    """Engine giọng đọc báo lỗi."""


class Cancelled(DubberError):
    """Người dùng bấm Hủy."""
