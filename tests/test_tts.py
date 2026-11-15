import pytest
from core.errors import TTSError
from core.tts import _retry


def test_retry_succeeds_on_transient_error(monkeypatch):
    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("Connection reset by peer: timeout")
        return "success"

    # Set base_delay to 0.001 to run instantly in tests
    res = _retry(flaky, tries=5, base_delay=0.001)
    assert res == "success"
    assert calls == 3


def test_retry_fails_immediately_on_non_transient_error():
    calls = 0

    def fatal():
        nonlocal calls
        calls += 1
        raise ValueError("Invalid voice name")

    with pytest.raises(TTSError, match="Invalid voice name"):
        _retry(fatal, tries=5, base_delay=0.001)
    assert calls == 1  # Không retry lỗi không thuộc danh mục tạm thời


def test_retry_raises_after_exhausting_all_attempts():
    calls = 0

    def always_fail():
        nonlocal calls
        calls += 1
        raise RuntimeError("No audio was received from server")

    with pytest.raises(TTSError, match="No audio was received"):
        _retry(always_fail, tries=4, base_delay=0.001)
    assert calls == 4
