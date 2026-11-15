"""TDTU Auto Dubber - Giao diện dòng lệnh (CLI).

Sử dụng:
  python cli.py --help
  python cli.py render --video in.mp4 --script script.txt --out out.mp4 --engine tone
  python cli.py clear-cache
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.config import (
    ROOT_DIR,
    SETTINGS_PATH,
    WORK_ROOT,
    DubSettings,
    Keys,
    load_env,
)
from core.errors import Cancelled, DubberError
from core.pipeline import DubbingPipeline
from core.reporter import Reporter
from core.subtitles import parse_script


def _create_reporter() -> Reporter:
    last_pct = [-1]

    def on_progress(pct: float, msg: str | None) -> None:
        p = int(pct)
        if p != last_pct[0] and (p % 10 == 0 or p == 100):
            last_pct[0] = p
            text = f" [{p:3d}%] {msg}" if msg else f" [{p:3d}%]"
            print(text, flush=True)

    def on_log(msg: str, level: str = "info") -> None:
        prefix = {
            "info": "ℹ️ ",
            "warn": "⚠️ ",
            "error": "❌ ",
            "ok": "✅ ",
        }.get(level, "")
        print(f"{prefix}{msg}", flush=True)

    return Reporter(on_log=on_log, on_progress=on_progress)


def cmd_render(args: argparse.Namespace) -> int:
    load_env()
    settings = DubSettings.load()

    # Cập nhật cấu hình từ tham số CLI nếu có
    if args.engine:
        settings.tts_engine = args.engine
    if args.voice:
        if settings.tts_engine == "elevenlabs":
            settings.elevenlabs_voice_id = args.voice
        else:
            settings.edge_voice = args.voice
    if args.target_lang:
        settings.target_lang = args.target_lang
    if args.source_lang:
        settings.source_lang = args.source_lang
    if args.mix_mode:
        settings.mix_mode = args.mix_mode
    if args.max_speed is not None:
        settings.max_speed = args.max_speed
    if args.burn_captions:
        settings.burn_captions = True
    if args.no_srt:
        settings.export_srt = False
    if args.keep_temp:
        settings.keep_temp = True

    script_path = Path(args.script)
    if not script_path.is_file():
        print(f"❌ Không tìm thấy file kịch bản: {script_path}", file=sys.stderr)
        return 1

    try:
        script_text = script_path.read_text(encoding="utf-8")
        segments = parse_script(script_text)
    except Exception as e:
        print(f"❌ Lỗi khi đọc file kịch bản: {e}", file=sys.stderr)
        return 1

    if not segments:
        print("❌ Kịch bản rỗng hoặc không đúng định dạng.", file=sys.stderr)
        return 1

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"❌ Không tìm thấy file video: {video_path}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else None
    rep = _create_reporter()

    pipeline = DubbingPipeline(settings, Keys.from_env(), reporter=rep)

    try:
        result = pipeline.render(video_path, segments, out_path)
        print(f"🎉 Xuất video thành công: {result}", flush=True)
        return 0
    except Cancelled:
        print("⛔ Tác vụ đã bị hủy.", file=sys.stderr)
        return 130
    except DubberError as e:
        print(f"❌ Lỗi xử lý: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"❌ Lỗi không mong đợi: {e}", file=sys.stderr)
        return 1
    finally:
        pipeline.cleanup()


def cmd_clear_cache(args: argparse.Namespace) -> int:
    deleted_count = 0
    # Dọn dẹp thư mục tạm
    if WORK_ROOT.exists():
        for item in WORK_ROOT.iterdir():
            try:
                if item.is_dir():
                    import shutil
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                deleted_count += 1
            except Exception as e:
                print(f"⚠️ Không thể xóa {item}: {e}")

    # Dọn dẹp thư mục cache (giữ lại .gitkeep)
    cache_dir = ROOT_DIR / "cache"
    if cache_dir.exists():
        for item in cache_dir.iterdir():
            if item.name == ".gitkeep":
                continue
            try:
                if item.is_dir():
                    import shutil
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                deleted_count += 1
            except Exception as e:
                print(f"⚠️ Không thể xóa {item}: {e}")

    print(f"✅ Đã dọn dẹp cache ({deleted_count} mục đã được xóa).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python cli.py",
        description="TDTU Auto Dubber - Công cụ tự động hóa lồng tiếng video.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Lệnh cần thực thi")

    # Lệnh render
    render_parser = subparsers.add_parser("render", help="Lồng tiếng cho video từ kịch bản có sẵn")
    render_parser.add_argument("--video", "-v", required=True, help="Đường dẫn file video đầu vào")
    render_parser.add_argument("--script", "-s", required=True, help="Đường dẫn file kịch bản (SRT, text, CSV)")
    render_parser.add_argument("--out", "-o", help="Đường dẫn video đầu ra (mặc định lưu tại thư mục output/)")
    render_parser.add_argument(
        "--engine",
        choices=["edge", "elevenlabs", "tone"],
        help="TTS engine (edge | elevenlabs | tone)",
    )
    render_parser.add_argument("--voice", help="Tên giọng đọc Edge-TTS hoặc Voice ID ElevenLabs")
    render_parser.add_argument("--target-lang", help="Mã ngôn ngữ đích (ví dụ: vi, en, ja, ko)")
    render_parser.add_argument("--source-lang", help="Mã ngôn ngữ gốc (ví dụ: auto, en, ja)")
    render_parser.add_argument(
        "--mix-mode",
        choices=["duck", "simple", "stems"],
        help="Chế độ trộn âm thanh (duck: giảm âm gốc khi AI nói, simple: giảm cố định, stems: tách nhạc)",
    )
    render_parser.add_argument("--max-speed", type=float, help="Tốc độ tăng tối đa khi câu bị tràn khung thời gian")
    render_parser.add_argument("--burn-captions", action="store_true", help="Đốt phụ đề trực tiếp vào video")
    render_parser.add_argument("--no-srt", action="store_true", help="Không xuất file phụ đề .srt kèm theo")
    render_parser.add_argument("--keep-temp", action="store_true", help="Giữ lại các file tạm trong quá trình xử lý")

    # Lệnh clear-cache
    subparsers.add_parser("clear-cache", help="Dọn dẹp cache và các file tạm trong temp_processing")

    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "render":
        sys.exit(cmd_render(args))
    elif args.command == "clear-cache":
        sys.exit(cmd_clear_cache(args))


if __name__ == "__main__":
    main()
