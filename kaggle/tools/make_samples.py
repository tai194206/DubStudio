"""Tạo file mẫu trong samples/ từ dữ liệu MINH HỌA tự viết (không phải kết quả nhận diện thật).

    python tools/make_samples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dubkaggle as dk  # noqa: E402

TOTAL = 42.0
# (bắt đầu, kết thúc, lời gốc, bản dịch, avg_logprob)
LINES = [
    (0.8, 3.6, "很久很久以前，有一座安静的小镇。", "Ngày xửa ngày xưa, có một thị trấn nhỏ yên bình.", -0.21),
    (3.9, 7.2, "小镇的中央有一座老旧的图书馆。", "Giữa thị trấn có một thư viện cũ kỹ.", -0.18),
    (7.8, 10.5, "管理员是一位白发苍苍的老爷爷。", "Người trông coi là một ông cụ tóc bạc trắng.", -0.25),
    (11.0, 12.6, "他每天都第一个到。", "Ngày nào ông cũng là người đến đầu tiên, mở cửa, lau từng kệ sách, rồi pha ấm trà nóng.", -0.31),
    (13.4, 17.0, "孩子们放学后最喜欢来这里。", "Bọn trẻ tan học là thích ghé qua đây nhất.", -0.19),
    (17.5, 21.0, "因为这里有讲不完的故事。", "Vì ở đây có những câu chuyện kể mãi không hết.", -1.35),
    (33.0, 37.5, "故事，才刚刚开始。", "Câu chuyện, mới chỉ bắt đầu thôi.", -0.22),
]


def build(index: int, a: float, b: float, text: str, logprob: float) -> dk.Seg:
    step = (b - a) / len(text)
    words = [dk.Word(ch, a + i * step, a + (i + 1) * step, 0.95) for i, ch in enumerate(text)]
    return dk.Seg(a, b, text, words, id=index, avg_logprob=logprob, no_speech_prob=0.02, compression_ratio=1.1)


def main() -> None:
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    cfg = dk.Config()
    segs = [build(i, a, b, src, lp) for i, (a, b, src, _vi, lp) in enumerate(LINES, 1)]
    dk.annotate_flags(segs, cfg, "zh")
    source = {"file": "vi_du_minh_hoa.mp4", "duration": TOTAL, "language": "zh", "language_probability": 0.99}
    dk.write_json(out / "transcript.sample.json", dk.to_dict(segs, source, cfg))
    (out / "script.sample.txt").write_text(dk.format_script(segs), encoding="utf-8")
    (out / "qc_report.sample.md").write_text(dk.qc_markdown(dk.build_qc(segs, TOTAL, cfg, "zh")), encoding="utf-8")

    vi = [dk.Seg(s.start, s.end, LINES[i][3], [], s.id, s.text) for i, s in enumerate(segs)]
    tcfg = dk.TranslateConfig()
    rows = dk.fit_rows(vi, TOTAL, dk.target_cps(tcfg), tcfg.speed_cap)
    summary = dk.fit_summary(rows, tcfg.speed_cap)
    (out / "script_vi.sample.txt").write_text(dk.format_script(vi), encoding="utf-8")
    (out / "fit_report.sample.md").write_text(dk.fit_markdown(rows, summary, vi, tcfg.speed_cap), encoding="utf-8")
    (out / "README.md").write_text(
        "# Mẫu minh họa\n\nCác file ở đây được sinh từ **dữ liệu tự viết** bằng `tools/make_samples.py` để bạn xem định dạng "
        "đầu ra. Đây KHÔNG phải kết quả nhận diện từ video thật.\n", encoding="utf-8")
    print("đã tạo mẫu tại", out)


if __name__ == "__main__":
    main()
