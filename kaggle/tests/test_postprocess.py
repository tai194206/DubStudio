import dubkaggle as dk
from dubkaggle import Config, Seg, Word


def words(texts, start=0.0, step=1.0, dur=0.9):
    return [Word(t, start + i * step, start + i * step + dur) for i, t in enumerate(texts)]


def test_time_formats():
    assert dk.fmt_srt(3661.5) == "01:01:01,500"
    assert dk.fmt_bracket(1.2) == "00:00:01.200"


def test_join_words_respects_language_spacing():
    ws = [Word(" Hello", 0, 1), Word(" world", 1, 2)]
    assert dk.join_words(ws, "en") == "Hello world"
    assert dk.join_words([Word("你好", 0, 1), Word("世界", 1, 2)], "zh") == "你好世界"


def test_count_chars_ignores_spaces_and_punct():
    assert dk.count_chars("你好，世界！") == 4
    assert dk.speech_len("xin   chào  ") == 8


def test_sanitize_sorts_clamps_and_fixes_overlap():
    segs = [Seg(5, 9, "b"), Seg(-1, 6, "a"), Seg(10, 10, "c")]
    out = dk.sanitize(segs, total=10.5)
    assert [s.text for s in out] == ["a", "b", "c"]
    assert out[0].start == 0 and out[0].end <= out[1].start        # het chong lan
    assert out[2].end > out[2].start                                # cau do dai 0 duoc noi ra
    assert all(s.end <= 10.5 for s in out)


def test_merge_short_joins_neighbors_with_small_gap():
    cfg = Config()
    segs = [Seg(0, 0.4, "你好"), Seg(0.5, 1.0, "世界"), Seg(5, 8, "另一句话比较长")]
    out = dk.merge_short(segs, cfg, "zh")
    assert [s.text for s in out] == ["你好世界", "另一句话比较长"]
    assert out[0].end == 1.0


def test_merge_short_does_not_cross_big_gap():
    cfg = Config()
    out = dk.merge_short([Seg(0, 0.3, "a"), Seg(3, 3.3, "b")], cfg, "en")
    assert len(out) == 2


def test_split_long_prefers_sentence_end_near_middle():
    texts = [f"w{i}" for i in range(16)]
    texts[7] = "w7。"
    s = Seg(0, 15.9, "".join(texts), words(texts))
    out = dk.split_long([s], Config(max_seg_seconds=8.0), "zh")
    assert len(out) == 2
    assert out[0].text.endswith("w7。")
    assert all(x.duration <= 8.0 for x in out)


def test_split_long_recurses_until_within_limits():
    texts = [f"w{i}" for i in range(40)]
    out = dk.split_long([Seg(0, 39.9, "".join(texts), words(texts))], Config(max_seg_seconds=8.0), "zh")
    assert len(out) >= 5 and all(x.duration <= 8.0 for x in out)
    assert "".join(x.text for x in out) == "".join(texts)           # khong mat chu


def test_split_long_without_words_falls_back_to_text():
    s = Seg(0, 20, "第一句话。第二句话也很长，第三句话。")
    out = dk.split_long([s], Config(max_seg_seconds=8.0, max_chars=15), "zh")
    assert len(out) >= 2
    assert out[0].start == 0 and abs(out[-1].end - 20) < 1e-6


def test_drop_hallucinations_requires_low_confidence():
    cfg = Config()
    bad = Seg(0, 2, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", avg_logprob=-1.4, no_speech_prob=0.7)
    real = Seg(3, 5, "感谢观看到这里的朋友", avg_logprob=-0.2, no_speech_prob=0.01)   # cau that, tin cay cao
    loop = Seg(6, 8, "啊" * 50, compression_ratio=4.2)
    kept, dropped = dk.drop_hallucinations([bad, real, loop], cfg)
    assert kept == [real] and len(dropped) == 2


def test_drop_hallucinations_strict_for_gap_recheck():
    cfg = Config()
    weak = Seg(0, 2, "一句话", avg_logprob=-1.5)
    ok = Seg(3, 5, "一句话", avg_logprob=-0.4, no_speech_prob=0.1)
    kept, dropped = dk.drop_hallucinations([weak, ok], cfg, strict=True)
    assert kept == [ok] and dropped == [weak]
    kept2, _ = dk.drop_hallucinations([weak], cfg, strict=False)
    assert kept2 == [weak]                                               # che do thuong thi giu


def test_collapse_repeats_keeps_first_two():
    segs = [Seg(i, i + 1, "好的好的。") for i in range(5)] + [Seg(6, 7, "不同")]
    out, dropped = dk.collapse_repeats(segs, max_repeat=2)
    assert dropped == 3 and [s.text for s in out][-1] == "不同" and len(out) == 3


def test_postprocess_returns_stats_and_renumbers():
    cfg = Config()
    segs = [Seg(0, 2, "感谢观看", avg_logprob=-1.2, no_speech_prob=0.9), Seg(3, 6, "正常的一句话", avg_logprob=-0.3)]
    out, stats = dk.postprocess(segs, cfg, "zh", total=10)
    assert stats["dropped_hallucinations"] == 1
    assert [s.id for s in out] == [1] and out[0].text == "正常的一句话"


def test_find_gaps_includes_leading_and_trailing():
    segs = [Seg(10, 12, "a"), Seg(13, 14, "b")]
    gaps = dk.find_gaps(segs, total=30, min_gap=5)
    assert gaps == [(0.0, 10), (14, 30)]
