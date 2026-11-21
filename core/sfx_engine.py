"""Động cơ nạp & tổng hợp hiệu ứng âm thanh điện ảnh (SFX) cho Story Studio 9:16.

Nguyên tắc:
- Ưu tiên tuyệt đối file âm thanh thật trong `search_dirs` (khớp tên gốc hoặc alias).
- Khi thiếu file trên đĩa, tổng hợp âm thanh điện ảnh giàu sub-bass & kết cấu tự nhiên bằng NumPy/FFT
  (tuyệt đối không để im lặng hay phát tiếng bíp thô).
- Không import tkinter/GUI trong core/.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from .audio_track import SR, fade_edges, read_wav
from .ffmpeg_utils import to_wav

SFX_AUDIO_EXTS: Tuple[str, ...] = (".wav", ".mp3", ".ogg", ".aac", ".m4a")

# Ánh xạ đầy đủ bộ tag SFX (7 tag cũ + toàn bộ tag điện ảnh V5.2) về nhóm âm thanh chuẩn
SFX_ALIASES: Dict[str, str] = {
    "boom": "boom",
    "dramatic_hit": "boom",
    "cinematic_impact": "cinematic_impact",
    "impact": "cinematic_impact",
    "artillery_fire": "artillery_fire",
    "cannon": "artillery_fire",
    "explosion": "artillery_fire",
    "gunshot": "gunshot",
    "whoosh": "whoosh",
    "transition": "whoosh",
    "tension_riser": "tension_riser",
    "riser": "tension_riser",
    "battle_charge": "battle_charge",
    "march": "battle_charge",
    "heartbeat": "heartbeat",
    "wind": "wind",
    "pop": "pop",
    "typing": "typing",
    "alarm": "alarm",
    "ship_horn": "ship_horn",
}

SFX_VALID_TAGS: frozenset[str] = frozenset({"none", *SFX_ALIASES.keys()})


def resolve_sfx_canonical(sfx_name: str) -> str:
    """Chuẩn hóa tên tag SFX và ánh xạ về tên chuẩn."""
    if not sfx_name:
        return "none"
    norm = re.sub(r"[^\w\-]", "", str(sfx_name).strip().lower())
    if not norm or norm == "none":
        return "none"
    return SFX_ALIASES.get(norm, norm)


def find_sfx_file(sfx_name: str, search_dirs: Sequence[Path]) -> Optional[Path]:
    """Tìm file âm thanh SFX thật trong thư mục chỉ định (ưu tiên tên gốc rồi tới alias chuẩn)."""
    canonical = resolve_sfx_canonical(sfx_name)
    if canonical == "none":
        return None

    norm_raw = re.sub(r"[^\w\-]", "", str(sfx_name).strip().lower())
    lookup_names = [norm_raw] if norm_raw else []
    if canonical not in lookup_names:
        lookup_names.append(canonical)

    for d in search_dirs:
        if not d or not d.is_dir():
            continue
        for name in lookup_names:
            for ext in SFX_AUDIO_EXTS:
                candidate = d / f"{name}{ext}"
                if candidate.is_file():
                    return candidate
    return None


def _rng_for_tag(tag: str) -> np.random.Generator:
    digest = hashlib.sha256(f"dubstudio_sfx_{tag}".encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "little")
    return np.random.default_rng(seed)


def _fft_bandpass(x: np.ndarray, sr: int, low_hz: float, high_hz: float) -> np.ndarray:
    """Lọc thông dải pha 0 mượt theo cửa sổ cosine trên miền tần số FFT."""
    n = x.size
    if n == 0:
        return x
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    spec = np.fft.rfft(x.astype(np.float64))

    gain = np.ones_like(freqs, dtype=np.float64)
    if low_hz > 0.0:
        lo_lo = max(1.0, low_hz * 0.65)
        lo_hi = max(lo_lo + 1.0, low_hz * 1.15)
        t_lo = np.clip((freqs - lo_lo) / (lo_hi - lo_lo), 0.0, 1.0)
        gain *= 0.5 * (1.0 - np.cos(np.pi * t_lo))
    if high_hz > 0.0 and high_hz < sr * 0.49:
        hi_lo = high_hz * 0.85
        hi_hi = min(sr * 0.5, high_hz * 1.35)
        if hi_hi > hi_lo:
            t_hi = np.clip((hi_hi - freqs) / (hi_hi - hi_lo), 0.0, 1.0)
            gain *= 0.5 * (1.0 - np.cos(np.pi * t_hi))
    gain[0] = 0.0  # Khử DC tuyệt đối
    out = np.fft.irfft(spec * gain, n=n)
    return out.astype(np.float32)


def _pitch_drop_sine(duration: float, sr: int, f_start: float, f_end: float, tau: float) -> np.ndarray:
    """Bộ dao động trượt tần số mượt theo hàm mũ cho cú đánh sub-bass sâu."""
    n = max(1, int(round(duration * sr)))
    t = np.arange(n, dtype=np.float64) / sr
    eff_tau = max(1e-4, float(tau))
    phase = 2.0 * np.pi * (f_end * t + (f_start - f_end) * eff_tau * (1.0 - np.exp(-t / eff_tau)))
    return np.sin(phase).astype(np.float32)


def _attack_decay_env(n: int, sr: int, attack_s: float, decay_tau: float, offset_s: float = 0.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sr - max(0.0, float(offset_s))
    active = t >= 0.0
    t_pos = np.maximum(0.0, t)
    env = np.where(
        active,
        (1.0 - np.exp(-t_pos / max(1e-4, attack_s))) * np.exp(-t_pos / max(1e-3, decay_tau)),
        0.0,
    )
    return env.astype(np.float32)


def _sine_bell(n: int, power: float = 1.0, start_frac: float = 0.0, end_frac: float = 1.0) -> np.ndarray:
    """Đường bao chuông sin^[power] an toàn với float64 (không bao giờ âm/NaN tại điểm cuối)."""
    u = np.linspace(0.0, 1.0, n, dtype=np.float64)
    span = max(1e-6, end_frac - start_frac)
    w = np.clip((u - start_frac) / span, 0.0, 1.0)
    s = np.clip(np.sin(np.pi * w), 0.0, 1.0)
    return (s ** float(power)).astype(np.float32)


def _finalize_sfx(x: np.ndarray, sr: int, target_peak: float = 0.85) -> np.ndarray:
    """Khử DC, làm mượt đầu/cuối tránh click và chuẩn hóa đỉnh âm thanh."""
    if x.size == 0:
        return x.astype(np.float32)
    y = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=True)
    y -= float(np.mean(y))
    n_in = min(int(round(sr * 0.005)), y.size // 4)
    n_out = min(int(round(sr * 0.030)), y.size // 3)
    if n_in > 0:
        y[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    if n_out > 0:
        y[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)
    peak = float(np.max(np.abs(y)))
    if peak > 1e-6:
        y *= float(target_peak) / peak
    return y


def _synth_boom(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.20
    n = int(round(dur * sr))
    t = np.arange(n, dtype=np.float32) / sr
    sub = _pitch_drop_sine(dur, sr, 96.0, 30.0, 0.14) * _attack_decay_env(n, sr, 0.006, 0.36)
    harm = np.sin(2.0 * np.pi * (58.0 + 95.0 * np.exp(-t / 0.06)) * t) * _attack_decay_env(n, sr, 0.004, 0.16)
    noise = rng.standard_normal(n).astype(np.float32)
    rumble = _fft_bandpass(noise, sr, 35.0, 260.0) * _attack_decay_env(n, sr, 0.012, 0.38)
    punch = _fft_bandpass(noise, sr, 110.0, 620.0) * _attack_decay_env(n, sr, 0.002, 0.055)
    return _finalize_sfx(sub * 1.05 + harm * 0.30 + rumble * 0.65 + punch * 0.55, sr)


def _synth_cinematic_impact(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.40
    n = int(round(dur * sr))
    sub = _pitch_drop_sine(dur, sr, 125.0, 28.0, 0.16) * _attack_decay_env(n, sr, 0.005, 0.44)
    noise = rng.standard_normal(n).astype(np.float32)
    body = _fft_bandpass(noise, sr, 75.0, 460.0) * _attack_decay_env(n, sr, 0.004, 0.28)
    crack = _fft_bandpass(noise, sr, 320.0, 1750.0) * _attack_decay_env(n, sr, 0.002, 0.06)
    tail = _fft_bandpass(noise, sr, 42.0, 190.0) * _attack_decay_env(n, sr, 0.035, 0.56, offset_s=0.025)
    mix = np.tanh((sub * 1.10 + body * 0.85 + crack * 0.65 + tail * 0.55) * 1.15).astype(np.float32)
    return _finalize_sfx(mix, sr)


def _synth_artillery_fire(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.65
    n = int(round(dur * sr))
    t = np.arange(n, dtype=np.float64) / sr
    noise = rng.standard_normal(n).astype(np.float32)
    muzzle_crack = _fft_bandpass(noise, sr, 120.0, 2400.0) * _attack_decay_env(n, sr, 0.002, 0.055)
    thump = _pitch_drop_sine(dur, sr, 88.0, 26.0, 0.14) * _attack_decay_env(n, sr, 0.005, 0.34)
    # Rền sấm chiến trường kéo dài với dao động nhẹ tự nhiên của địa hình
    flutter = (0.78 + 0.22 * np.sin(2.0 * np.pi * 6.5 * t + 0.4)).astype(np.float32)
    thunder_tail = _fft_bandpass(noise, sr, 40.0, 340.0) * _attack_decay_env(n, sr, 0.035, 0.54, offset_s=0.03) * flutter
    blast = np.tanh((muzzle_crack * 1.10 + thump * 1.15 + thunder_tail * 0.95) * 1.25).astype(np.float32)
    return _finalize_sfx(blast, sr)


def _synth_gunshot(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 0.50
    n = int(round(dur * sr))
    noise = rng.standard_normal(n).astype(np.float32)
    crack = _fft_bandpass(noise, sr, 350.0, 3800.0) * _attack_decay_env(n, sr, 0.001, 0.022)
    body = _pitch_drop_sine(dur, sr, 165.0, 52.0, 0.035) * _attack_decay_env(n, sr, 0.002, 0.055)
    reverb = _fft_bandpass(noise, sr, 160.0, 1400.0) * _attack_decay_env(n, sr, 0.012, 0.11, offset_s=0.055)
    shot = np.tanh((crack * 1.25 + body * 0.75 + reverb * 0.42) * 1.20).astype(np.float32)
    return _finalize_sfx(shot, sr)


def _synth_whoosh(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 0.68
    n = int(round(dur * sr))
    bell = _sine_bell(n, 1.55)
    air_env = _sine_bell(n, 1.80, start_frac=0.12, end_frac=0.92)
    noise = rng.standard_normal(n).astype(np.float32)
    low_rush = _fft_bandpass(noise, sr, 110.0, 480.0) * bell
    high_air = _fft_bandpass(noise, sr, 480.0, 2200.0) * air_env
    sub_doppler = _pitch_drop_sine(dur, sr, 82.0, 42.0, dur * 0.75) * bell * 0.18
    return _finalize_sfx(low_rush * 1.05 + high_air * 0.75 + sub_doppler, sr)


def _synth_tension_riser(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.80
    n = int(round(dur * sr))
    u = np.linspace(0.0, 1.0, n, dtype=np.float64)
    # Tăng kịch tính tới ~94% thời lượng rồi nhả mượt để không gây tiếng tách
    swell = np.where(u <= 0.94, (u / 0.94) ** 1.85, np.cos(((u - 0.94) / 0.06) * (np.pi / 2.0)))
    swell = swell.astype(np.float32)

    freq = 48.0 + 56.0 * (u ** 1.4)
    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    sub_hum = (np.sin(phase) + 0.35 * np.sin(phase * 1.5)).astype(np.float32)

    noise = rng.standard_normal(n).astype(np.float32)
    dark_air = _fft_bandpass(noise, sr, 140.0, 950.0)
    tension_sheen = _fft_bandpass(noise, sr, 650.0, 1950.0) * (u.astype(np.float32) ** 2.2)
    combined = swell * (sub_hum * 0.45 + dark_air * 0.75 + tension_sheen * 0.70)
    return _finalize_sfx(combined, sr)


def _synth_battle_charge(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.65
    n = int(round(dur * sr))
    noise = rng.standard_normal(n).astype(np.float32)
    bed = _fft_bandpass(noise, sr, 42.0, 260.0) * _sine_bell(n, 0.75)

    out = bed * 0.48
    drum_noise = _fft_bandpass(noise, sr, 95.0, 560.0)
    for idx_hit, offset_s in enumerate((0.02, 0.36, 0.68, 0.98)):
        amp = 0.85 + 0.08 * idx_hit
        drum_tone = np.zeros(n, dtype=np.float32)
        idx0 = int(round(offset_s * sr))
        rem = n - idx0
        if rem > 16:
            seg_tone = _pitch_drop_sine(rem / sr, sr, 84.0, 36.0, 0.085) * _attack_decay_env(rem, sr, 0.004, 0.20)
            seg_skin = drum_noise[idx0:] * _attack_decay_env(rem, sr, 0.003, 0.10)
            drum_tone[idx0:] = seg_tone * 0.95 + seg_skin * 0.75
        out += amp * drum_tone
    return _finalize_sfx(np.tanh(out * 1.15).astype(np.float32), sr)


def _synth_heartbeat(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.05
    n = int(round(dur * sr))
    noise = _fft_bandpass(rng.standard_normal(n).astype(np.float32), sr, 40.0, 165.0)
    out = np.zeros(n, dtype=np.float32)
    for offset_s, f_hi, f_lo, gain in ((0.02, 62.0, 34.0, 1.0), (0.30, 52.0, 30.0, 0.78)):
        idx0 = int(round(offset_s * sr))
        rem = n - idx0
        if rem > 16:
            pulse = _pitch_drop_sine(rem / sr, sr, f_hi, f_lo, 0.065) * _attack_decay_env(rem, sr, 0.008, 0.095)
            thud = noise[idx0:] * _attack_decay_env(rem, sr, 0.006, 0.075)
            out[idx0:] += gain * (pulse + thud * 0.65)
    return _finalize_sfx(out, sr)


def _synth_wind(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.65
    n = int(round(dur * sr))
    noise = rng.standard_normal(n).astype(np.float32)
    gust = _fft_bandpass(noise, sr, 150.0, 820.0) * _sine_bell(n, 1.35)
    ridge = _fft_bandpass(noise, sr, 380.0, 1250.0) * _sine_bell(n, 2.20) * 0.45
    return _finalize_sfx(gust + ridge, sr)


def _synth_pop(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 0.20
    n = int(round(dur * sr))
    t = np.arange(n, dtype=np.float64) / sr
    freq = 360.0 + 480.0 * (1.0 - np.exp(-t / 0.02))
    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    tone = np.sin(phase).astype(np.float32) * _attack_decay_env(n, sr, 0.002, 0.028)
    click = _fft_bandpass(rng.standard_normal(n).astype(np.float32), sr, 350.0, 1400.0) * _attack_decay_env(n, sr, 0.001, 0.015)
    return _finalize_sfx(tone + click * 0.35, sr, target_peak=0.72)


def _synth_typing(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 0.68
    n = int(round(dur * sr))
    noise = _fft_bandpass(rng.standard_normal(n).astype(np.float32), sr, 420.0, 2200.0)
    out = np.zeros(n, dtype=np.float32)
    for idx_k, offset_s in enumerate((0.02, 0.14, 0.25, 0.40, 0.53)):
        idx0 = int(round(offset_s * sr))
        rem = n - idx0
        if rem > 16:
            env = _attack_decay_env(rem, sr, 0.001, 0.014)
            tap = (noise[idx0:] + 0.35 * _pitch_drop_sine(rem / sr, sr, 460.0, 160.0, 0.008)) * env
            out[idx0:] += (0.8 + 0.08 * (idx_k % 2)) * tap
    return _finalize_sfx(out, sr, target_peak=0.70)


def _synth_alarm(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 0.95
    n = int(round(dur * sr))
    t = np.arange(n, dtype=np.float64) / sr
    base = (np.sin(2.0 * np.pi * 520.0 * t) + 0.45 * np.sin(2.0 * np.pi * 780.0 * t)).astype(np.float32)
    base = _fft_bandpass(base, sr, 300.0, 1200.0)
    env = np.zeros(n, dtype=np.float32)
    for st in (0.02, 0.46):
        win = (t >= st) & (t < st + 0.28)
        if np.any(win):
            local_u = (t[win] - st) / 0.28
            env[win] = (np.clip(np.sin(np.pi * local_u), 0.0, 1.0) ** 0.8).astype(np.float32)
    return _finalize_sfx(base * env, sr, target_peak=0.68)


def _synth_ship_horn(sr: int, rng: np.random.Generator) -> np.ndarray:
    dur = 1.40
    n = int(round(dur * sr))
    t = np.arange(n, dtype=np.float64) / sr
    horn = (
        np.sin(2.0 * np.pi * 96.0 * t)
        + 0.72 * np.sin(2.0 * np.pi * 144.0 * t)
        + 0.38 * np.sin(2.0 * np.pi * 192.0 * t)
    ).astype(np.float32)
    breath = _fft_bandpass(rng.standard_normal(n).astype(np.float32), sr, 120.0, 420.0)
    env = _sine_bell(n, 0.55)
    return _finalize_sfx((horn + breath * 0.22) * env, sr, target_peak=0.78)


_SYNTH_BUILDERS = {
    "boom": _synth_boom,
    "cinematic_impact": _synth_cinematic_impact,
    "artillery_fire": _synth_artillery_fire,
    "gunshot": _synth_gunshot,
    "whoosh": _synth_whoosh,
    "tension_riser": _synth_tension_riser,
    "battle_charge": _synth_battle_charge,
    "heartbeat": _synth_heartbeat,
    "wind": _synth_wind,
    "pop": _synth_pop,
    "typing": _synth_typing,
    "alarm": _synth_alarm,
    "ship_horn": _synth_ship_horn,
}


def synthesize_sfx(sfx_name: str, sample_rate: int = SR) -> np.ndarray:
    """Tổng hợp SFX điện ảnh sạch, trầm và có lực theo tên tag chuẩn."""
    canonical = resolve_sfx_canonical(sfx_name)
    if canonical == "none":
        return np.zeros(0, dtype=np.float32)
    builder = _SYNTH_BUILDERS.get(canonical, _synth_cinematic_impact)
    rng = _rng_for_tag(canonical)
    return builder(int(sample_rate), rng)


def load_or_synthesize_sfx(
    sfx_name: str,
    search_dirs: Sequence[Path],
    work_dir: Path,
    *,
    sample_rate: int = SR,
    index: int = 0,
) -> Tuple[Optional[np.ndarray], str]:
    """Nạp file SFX thật từ `search_dirs`; nếu thiếu trên đĩa thì tổng hợp fallback sạch.

    Trả về `(samples_float32, source_tag)` với `source_tag` dạng `'file:...'` hoặc `'synth:...'`.
    """
    canonical = resolve_sfx_canonical(sfx_name)
    if canonical == "none":
        return None, "none"

    sfx_file = find_sfx_file(sfx_name, search_dirs)
    if sfx_file is not None:
        safe_name = re.sub(r"[^\w\-]", "_", canonical)
        tmp_wav = Path(work_dir) / f"sfx_{index:03d}_{safe_name}.wav"
        to_wav(sfx_file, tmp_wav, sample_rate, 1)
        data = read_wav(tmp_wav)
        if data.size > 0:
            data = data - float(np.mean(data))
            peak = float(np.max(np.abs(data)))
            # Nâng nhẹ nếu file gốc trên đĩa thu quá nhỏ, giới hạn gain tối đa 4x tránh nâng nhiễu nền
            if 0.005 < peak < 0.70:
                gain = min(4.0, 0.80 / peak)
                data = data * gain
            return fade_edges(data), f"file:{sfx_file.name}"

    return synthesize_sfx(canonical, sample_rate=sample_rate), f"synth:{canonical}"
