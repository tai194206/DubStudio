#!/usr/bin/env python3
"""Công cụ săn, kiểm duyệt trực quan và chuẩn hóa Ảnh & Video tư liệu lịch sử thật (V5.2).

Nguồn hỗ trợ (ưu tiên Public Domain / giấy phép mở):
- Wikimedia Commons API (`--mode images`, `--mode videos`)
- Internet Archive API (`archive.org`, `--mode videos`)

Quy trình chuẩn:
1. Săn ứng viên vào `automation/debug/archive_candidates/<ten_tap>/images|videos/` kèm metadata & contact sheet 6 khung.
2. Kiểm duyệt trực quan (đúng thời kỳ lịch sử, KHÔNG watermark/logo đài truyền hình).
3. Chuẩn hóa & nạp vào `<episode_dir>/assets/{id:03d}.jpg|mp4` (`--mode commit-image` / `--mode commit-video`),
   tự động lưu nguồn + giấy phép vào `<episode_dir>/archive_sources.json`.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CANDIDATES_ROOT = REPO_ROOT / "automation" / "debug" / "archive_candidates"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata/{identifier}"

USER_AGENT = "DubStudioArchiveFetcher/1.0 (https://github.com/tai194206/DubStudio; historical-archive-research)"
REQUEST_TIMEOUT = 25

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".ogv", ".mov"}
CANDIDATE_MAX_LONG_EDGE = 1600

# Bộ lọc chống logo đài truyền hình, watermark stock thương mại và file biểu tượng UI
FORBIDDEN_MEDIA_RE = re.compile(
    r"(?:\b(?:vtv[1-9]?|ina|getty\s*images?|shutterstock|istockphoto|alamy|corbis|"
    r"reuters|ap\s*photo|ap\s*archive|associated\s*press|british\s*path[eé]|"
    r"afp\s*photo|pond5|dreamstime|123rf|periscope\s*film|all\s*rights\s*reserved)\b|"
    r"watermark|©\s*(?:vtv|ina|reuters|ap|afp|getty))",
    re.IGNORECASE,
)

UI_ICON_TITLE_RE = re.compile(
    r"(?:commons-logo|wiki[^a-z]*logo|nuvola|crystal_clear|flag_icon|stub[_ ]?icon|ambox_)",
    re.IGNORECASE,
)

ARCHIVAL_OVERRIDE_RE = re.compile(
    r"\b(?:contemporary\s+photo(?:graph)?|archival|archive|archived|historical\s+photo(?:graph)?|wartime\s+photo(?:graph)?)\b",
    re.IGNORECASE,
)
HISTORICAL_YEAR_RE = re.compile(r"\b19[4-7]\d\b")
PUBLIC_DOMAIN_RE = re.compile(r"(?:\bpublic\s+domain\b|\bpd[-_\s])", re.IGNORECASE)

MODERN_SITE_RE = re.compile(
    r"\b(?:souvenirs?|fabrics?\s+on\s+display|tourists?|memorials?|monuments?|statues?|wax\s+figures?|dioramas?|gift\s+shops?)\b",
    re.IGNORECASE,
)
PANORAMA_BUNKER_RE = re.compile(r"panorama\s+of\b[^|\n]{0,100}\bbunker\b", re.IGNORECASE)
INTERIOR_OF_RE = re.compile(r"\binterior\s+of\b", re.IGNORECASE)
MODERN_YEAR_RE = re.compile(r"\b20(?:1\d|2\d)\b")


def is_modern_tourist_or_site_media(
    title: str,
    description: str = "",
    year_or_date: str = "",
    license_str: str = "",
) -> Tuple[bool, str]:
    """Nhận diện ảnh du lịch/bảo tàng/di tích chụp hiện đại nhưng vẫn giữ qua ảnh gốc lưu trữ."""
    title_desc = f"{title} {description}".strip()
    all_with_year = f"{title_desc} {year_or_date}".strip()

    if ARCHIVAL_OVERRIDE_RE.search(title_desc):
        return False, ""
    if HISTORICAL_YEAR_RE.search(all_with_year):
        return False, ""
    if PUBLIC_DOMAIN_RE.search(license_str or ""):
        return False, ""

    m_mod = MODERN_SITE_RE.search(title_desc)
    if m_mod:
        return True, f"Ảnh di tích/du lịch hiện đại ('{m_mod.group(0)}')"
    m_pan = PANORAMA_BUNKER_RE.search(title_desc)
    if m_pan:
        return True, f"Ảnh toàn cảnh hầm/di tích hiện đại ('{m_pan.group(0)}')"
    if INTERIOR_OF_RE.search(title_desc) and MODERN_YEAR_RE.search(all_with_year):
        return True, "Ảnh nội thất chụp hiện đại (201x/202x)"
    return False, ""


def _find_media_binary(name: str) -> str:
    """Tìm ffmpeg/ffprobe từ core.ffmpeg_utils (nếu sẵn) hoặc PATH hệ thống."""
    try:
        from core.ffmpeg_utils import find_binary

        found = find_binary(name)
        if found:
            return found
    except Exception:
        pass
    found_sys = shutil.which(name)
    if found_sys:
        return found_sys
    raise RuntimeError(f"Không tìm thấy '{name}' trên hệ thống.")


def _subprocess_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _strip_html(raw: Any) -> str:
    if raw is None:
        return ""
    text = html.unescape(str(raw))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_slug(text: str, max_len: int = 54) -> str:
    base = re.sub(r"^File:", "", str(text).strip(), flags=re.IGNORECASE)
    base = Path(base).stem if "." in base else base
    slug = re.sub(r"[^A-Za-z0-9\-_]+", "_", base).strip("_")
    return (slug[:max_len] or "media").strip("_") or "media"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,*/*"})
    return s


def _resolve_candidates_dir(args: argparse.Namespace, kind: str) -> Path:
    if getattr(args, "out_dir", None):
        out = Path(args.out_dir).resolve()
    else:
        if getattr(args, "episode_name", None):
            ep_name = _safe_slug(args.episode_name, max_len=64)
        elif getattr(args, "episode_dir", None):
            ep_name = Path(args.episode_dir).resolve().name
        else:
            ep_name = "default_episode"
        out = (DEFAULT_CANDIDATES_ROOT / ep_name / kind).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _is_clean_open_license(license_name: str, usage_terms: str, copyrighted: str, combined_text: str) -> Tuple[bool, str]:
    """Kiểm tra sơ bộ giấy phép & chặn nhãn watermark/bản quyền đóng rõ ràng."""
    if FORBIDDEN_MEDIA_RE.search(combined_text):
        m = FORBIDDEN_MEDIA_RE.search(combined_text)
        return False, f"Phát hiện từ khóa bản quyền/logo đài bị cấm: '{m.group(0)}'"
    if "true" in copyrighted.lower() and not any(
        tok in (license_name + " " + usage_terms).lower()
        for tok in ("cc", "public domain", "pd", "gfdl", "open", "zero")
    ):
        return False, f"File đánh dấu Copyrighted ({license_name or usage_terms})"
    return True, ""


def _download_file(session: requests.Session, url: str, dest: Path, max_bytes: int = 0) -> Tuple[bool, str]:
    """Tải file stream an toàn, giới hạn dung lượng để không nghẽn mạng."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True) as resp:
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            cl = resp.headers.get("Content-Length")
            if max_bytes > 0 and cl and cl.isdigit() and int(cl) > max_bytes:
                return False, f"Dung lượng {int(cl)/(1024*1024):.1f}MB vượt giới hạn {max_bytes/(1024*1024):.0f}MB"
            total = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if max_bytes > 0 and total > max_bytes:
                        tmp.unlink(missing_ok=True)
                        return False, f"Vượt dung lượng tối đa {max_bytes/(1024*1024):.0f}MB"
                    f.write(chunk)
        if tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            return False, "File rỗng 0 byte"
        os.replace(tmp, dest)
        return True, ""
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return False, str(e)


def _probe_video(ffprobe_bin: str, path: Path) -> Dict[str, Any]:
    """Đọc width, height, duration, codec và luồng audio bằng ffprobe JSON."""
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_subprocess_flags(),
    )
    if res.returncode != 0 or not res.stdout.strip():
        return {}
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = 0.0
    for src in (fmt.get("duration"), v_stream.get("duration") if v_stream else None):
        if src:
            try:
                dur = float(src)
                break
            except (ValueError, TypeError):
                pass
    return {
        "has_video": v_stream is not None,
        "has_audio": a_stream is not None,
        "width": int(v_stream.get("width", 0)) if v_stream else 0,
        "height": int(v_stream.get("height", 0)) if v_stream else 0,
        "codec": str(v_stream.get("codec_name", "")) if v_stream else "",
        "duration": dur,
    }


def _load_pil_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for p in (
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _normalize_candidate_jpeg(
    src_path: Path,
    *,
    max_long_edge: int = CANDIDATE_MAX_LONG_EDGE,
    quality: int = 90,
) -> Tuple[Optional[Path], Tuple[int, int], str]:
    """Chuẩn hóa ảnh ứng viên/contact sheet sang RGB JPEG sạch (<= max_long_edge), tự xóa nếu rỗng/lỗi."""
    if not src_path.is_file() or src_path.stat().st_size == 0:
        src_path.unlink(missing_ok=True)
        return None, (0, 0), "File rỗng hoặc không tồn tại"

    dst_jpg = src_path.with_suffix(".jpg")
    tmp_jpg = dst_jpg.with_name(f".{dst_jpg.stem}_norm_tmp.jpg")
    replaced = False

    try:
        with Image.open(src_path) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                rgb = bg
            else:
                rgb = img.convert("RGB")

            if max(rgb.size) > max_long_edge:
                rgb.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)

            w, h = rgb.size
            if w <= 0 or h <= 0:
                raise ValueError("Kích thước ảnh không hợp lệ")

            rgb.save(tmp_jpg, format="JPEG", quality=quality, optimize=True)

        if not tmp_jpg.is_file() or tmp_jpg.stat().st_size == 0:
            raise ValueError("File JPEG chuẩn hóa đầu ra rỗng")

        with Image.open(tmp_jpg) as verify_img:
            verify_img.verify()
        with Image.open(tmp_jpg) as check_img:
            check_img.load()
            if check_img.format != "JPEG" or check_img.mode != "RGB" or check_img.size != (w, h) or max(check_img.size) > max_long_edge:
                raise ValueError(
                    f"JPEG sau chuẩn hóa không khớp thông số (format={check_img.format}, mode={check_img.mode}, size={check_img.size})"
                )

        os.replace(tmp_jpg, dst_jpg)
        replaced = True
        if src_path.resolve() != dst_jpg.resolve():
            src_path.unlink(missing_ok=True)
        return dst_jpg, (w, h), ""
    except Exception as e:
        tmp_jpg.unlink(missing_ok=True)
        src_path.unlink(missing_ok=True)
        if dst_jpg != src_path and (replaced or (dst_jpg.is_file() and dst_jpg.stat().st_size == 0)):
            dst_jpg.unlink(missing_ok=True)
        return None, (0, 0), str(e)


# ------------------------------------------------------------------ Wikimedia Commons Search
def _search_wikimedia(
    session: requests.Session,
    queries: Sequence[str],
    *,
    filetype: str,
    limit: int,
    exclude_modern: bool = True,
) -> List[Dict[str, Any]]:
    """Truy vấn Wikimedia Commons API với nhiều từ khóa Việt/Anh/Pháp, khử trùng URL/pageid."""
    collected: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    per_query = max(limit * 2, 16)

    for q in queries:
        q_clean = str(q).strip()
        if not q_clean:
            continue
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": f"{q_clean} filetype:{filetype}",
            "gsrlimit": str(min(50, per_query)),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata|sha1",
            "iiextmetadatafilter": "Artist|DateTimeOriginal|ImageDescription|LicenseShortName|UsageTerms|Credit|Copyrighted",
        }
        try:
            resp = session.get(WIKIMEDIA_API_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"⚠️ Lỗi truy vấn Wikimedia cho '{q_clean}': {e}", file=sys.stderr)
            continue

        pages = list((payload.get("query") or {}).get("pages", {}).values())
        pages.sort(key=lambda item: item.get("index", 9999))

        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = str(info.get("url") or "").strip()
            title = str(page.get("title") or "").strip()
            if not url or UI_ICON_TITLE_RE.search(title):
                continue

            extmeta = info.get("extmetadata") or {}
            author = _strip_html((extmeta.get("Artist") or {}).get("value")) or "Unknown"
            date_orig = _strip_html((extmeta.get("DateTimeOriginal") or {}).get("value"))
            desc = _strip_html((extmeta.get("ImageDescription") or {}).get("value"))
            lic_short = _strip_html((extmeta.get("LicenseShortName") or {}).get("value"))
            usage_terms = _strip_html((extmeta.get("UsageTerms") or {}).get("value"))
            credit = _strip_html((extmeta.get("Credit") or {}).get("value"))
            copyrighted = _strip_html((extmeta.get("Copyrighted") or {}).get("value"))
            license_str = lic_short or usage_terms or "Check Wikimedia page"

            combined_meta = " | ".join([title, desc, author, credit, license_str])
            ok_lic, reason = _is_clean_open_license(lic_short, usage_terms, copyrighted, combined_meta)
            if not ok_lic:
                print(f"   ⏭️ Bỏ qua '{title}': {reason}")
                continue

            if exclude_modern:
                is_mod, mod_reason = is_modern_tourist_or_site_media(title, desc, date_orig, license_str)
                if is_mod:
                    print(f"   ⏭️ Bỏ qua '{title}' (--exclude-modern): {mod_reason}")
                    continue

            dedup_key = str(info.get("sha1") or url)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
            collected.append(
                {
                    "provider": "wikimedia_commons",
                    "query": q_clean,
                    "title": title,
                    "author": author[:180],
                    "year_or_date": date_orig[:80],
                    "description": desc[:400],
                    "license": license_str[:120],
                    "mime": str(info.get("mime") or "").lower(),
                    "ext": ext,
                    "width": int(info.get("width") or 0),
                    "height": int(info.get("height") or 0),
                    "size_bytes": int(info.get("size") or 0),
                    "sha1": str(info.get("sha1") or ""),
                    "url": url,
                    "page_url": str(info.get("descriptionurl") or url),
                }
            )
    return collected


# ------------------------------------------------------------------ Mode 1: Images
def run_mode_images(args: argparse.Namespace) -> int:
    queries = list(args.query or [])
    if not queries:
        raise ValueError("Cần truyền ít nhất 1 --query khi dùng --mode images.")

    out_dir = _resolve_candidates_dir(args, "images")
    session = _make_session()
    raw_items = _search_wikimedia(
        session,
        queries,
        filetype="bitmap",
        limit=args.limit,
        exclude_modern=getattr(args, "exclude_modern", True),
    )

    accepted: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    for item in raw_items:
        if len(accepted) >= args.limit:
            break
        ext = item["ext"]
        mime = item["mime"]
        long_edge = max(item["width"], item["height"])
        if ext not in ALLOWED_IMAGE_EXTS or (mime and mime not in ALLOWED_IMAGE_MIMES):
            continue
        if long_edge < args.min_long_edge:
            continue

        idx = len(accepted) + 1
        fname = f"cand_{idx:02d}_{_safe_slug(item['title'])}{ext}"
        dest = out_dir / fname

        if args.dry_run:
            item["saved_file"] = ""
            item["sha256"] = ""
            accepted.append(item)
            print(f"[DRY-RUN IMG #{idx}] {item['width']}x{item['height']} | {item['license']} | {item['title']}")
            continue

        ok, err = _download_file(session, item["url"], dest, max_bytes=35 * 1024 * 1024)
        if not ok:
            print(f"   ⚠️ Lỗi tải '{item['title']}': {err}")
            continue

        if not dest.is_file() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            print(f"   ⚠️ Bỏ qua '{item['title']}': file tải về rỗng 0 byte")
            continue

        try:
            with Image.open(dest) as orig_img:
                orig_img.load()
                orig_w, orig_h = ImageOps.exif_transpose(orig_img).size
        except Exception as e:
            dest.unlink(missing_ok=True)
            print(f"   ⚠️ Bỏ qua '{item['title']}': file ảnh lỗi ({e})")
            continue

        if max(orig_w, orig_h) < args.min_long_edge:
            dest.unlink(missing_ok=True)
            continue

        norm_path, (w, h), norm_err = _normalize_candidate_jpeg(dest, max_long_edge=CANDIDATE_MAX_LONG_EDGE)
        if norm_path is None:
            print(f"   ⚠️ Bỏ qua '{item['title']}': lỗi chuẩn hóa RGB JPEG ({norm_err})")
            continue

        sha256 = _sha256_file(norm_path)
        if sha256 in seen_sha256:
            norm_path.unlink(missing_ok=True)
            continue
        seen_sha256.add(sha256)

        item["width"], item["height"] = w, h
        item["ext"] = ".jpg"
        item["mime"] = "image/jpeg"
        item["sha256"] = sha256
        item["saved_file"] = norm_path.name
        accepted.append(item)
        print(f"✓ IMG #{idx}: {norm_path.name} ({w}x{h} | {item['license']} | tác giả: {item['author'][:40]})")

    _write_candidates_manifest(out_dir, accepted, queries=queries, dry_run=args.dry_run, kind="images")
    print(f"\n📂 Đã lưu {len(accepted)} ứng viên ảnh tại: {out_dir}")
    return 0


# ------------------------------------------------------------------ Mode 2: Videos & Contact Sheet
def _search_internet_archive_videos(
    session: requests.Session,
    queries: Sequence[str],
    *,
    limit: int,
    max_bytes: int,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    rows = max(limit * 3, 12)

    for q in queries:
        q_clean = str(q).strip()
        if not q_clean:
            continue
        params = [
            ("q", f"({q_clean}) AND mediatype:(movies)"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "year"),
            ("fl[]", "date"),
            ("fl[]", "description"),
            ("fl[]", "creator"),
            ("fl[]", "licenseurl"),
            ("fl[]", "rights"),
            ("rows", str(min(40, rows))),
            ("page", "1"),
            ("output", "json"),
        ]
        try:
            resp = session.get(IA_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            docs = ((resp.json().get("response") or {}).get("docs") or [])
        except Exception as e:
            print(f"⚠️ Lỗi tìm Internet Archive cho '{q_clean}': {e}", file=sys.stderr)
            continue

        for doc in docs:
            ident = str(doc.get("identifier") or "").strip()
            if not ident or ident in seen_ids:
                continue
            seen_ids.add(ident)

            title = str(doc.get("title") or ident).strip()
            year_val = str(doc.get("year") or doc.get("date") or "").strip()
            desc = _strip_html(doc.get("description"))
            creator = _strip_html(doc.get("creator")) or "Internet Archive"
            lic_url = str(doc.get("licenseurl") or "").strip()
            rights = _strip_html(doc.get("rights"))

            combined = " | ".join([ident, title, desc, creator, lic_url, rights])
            if FORBIDDEN_MEDIA_RE.search(combined):
                print(f"   ⏭️ Bỏ qua IA '{ident}': có từ khóa bản quyền/watermark đài.")
                continue

            # Đọc danh sách file của identifier để chọn MP4/WebM tốt nhất trong ngưỡng dung lượng
            try:
                meta_resp = session.get(IA_METADATA_URL.format(identifier=ident), timeout=REQUEST_TIMEOUT)
                meta_resp.raise_for_status()
                meta_json = meta_resp.json()
            except Exception as e:
                print(f"   ⚠️ Lỗi đọc metadata IA '{ident}': {e}", file=sys.stderr)
                continue

            files = meta_json.get("files") or []
            best_file = _pick_best_ia_video_file(files, max_bytes=max_bytes)
            if not best_file:
                continue

            fname = str(best_file.get("name") or "")
            ext = Path(fname).suffix.lower() or ".mp4"
            dl_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}"
            lic_label = lic_url or rights or "Internet Archive (kiểm tra trang gốc)"
            candidates.append(
                {
                    "provider": "internet_archive",
                    "identifier": ident,
                    "query": q_clean,
                    "title": title[:180],
                    "author": creator[:140],
                    "year_or_date": year_val[:60],
                    "description": desc[:400],
                    "license": lic_label[:160],
                    "ext": ext,
                    "width": _safe_int(best_file.get("width")),
                    "height": _safe_int(best_file.get("height")),
                    "size_bytes": _safe_int(best_file.get("size")),
                    "url": dl_url,
                    "page_url": f"https://archive.org/details/{ident}",
                }
            )
            if len(candidates) >= limit * 2:
                break
    return candidates


def _safe_int(val: Any) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def _pick_best_ia_video_file(files: Sequence[Dict[str, Any]], *, max_bytes: int) -> Optional[Dict[str, Any]]:
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for f in files:
        name = str(f.get("name") or "")
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_VIDEO_EXTS:
            continue
        if "_thumbs" in name.lower() or ".ia." in name.lower() and ext == ".gif":
            continue
        sz = _safe_int(f.get("size"))
        if sz <= 0 or (max_bytes > 0 and sz > max_bytes):
            continue
        fmt = str(f.get("format") or "").lower()
        score = 0
        if ext == ".mp4":
            score += 100
        elif ext == ".webm":
            score += 80
        elif ext == ".mov":
            score += 60
        if "h.264" in fmt or "mpeg4" in fmt:
            score += 30
        if str(f.get("source") or "").lower() == "original" and ext in (".mp4", ".webm"):
            score += 15
        h = _safe_int(f.get("height"))
        if 360 <= h <= 1080:
            score += 20
        scored.append((score, f))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def create_video_contact_sheet(
    ffmpeg_bin: str,
    ffprobe_bin: str,
    video_path: Path,
    out_jpg: Path,
    meta: Dict[str, Any],
) -> Optional[Path]:
    """Xuất lưới 6 khung hình (3x2) kèm nhãn mốc giây để duyệt thời kỳ & watermark."""
    info = _probe_video(ffprobe_bin, video_path)
    dur = float(info.get("duration") or 0.0)
    if not info.get("has_video") or dur <= 0.0:
        return None

    fracs = (0.05, 0.22, 0.40, 0.58, 0.76, 0.92)
    tile_w, tile_h = 420, 236
    cols, rows = 3, 2
    pad = 12
    header_h = 88

    canvas_w = cols * tile_w + (cols + 1) * pad
    canvas_h = header_h + rows * tile_h + (rows + 1) * pad
    sheet = Image.new("RGB", (canvas_w, canvas_h), (22, 24, 29))
    draw = ImageDraw.Draw(sheet)

    font_title = _load_pil_font(20)
    font_sub = _load_pil_font(15)
    font_badge = _load_pil_font(16)

    title = str(meta.get("title") or video_path.name)[:78]
    year_str = str(meta.get("year_or_date") or "?")
    lic_str = str(meta.get("license") or "?")[:62]
    res_str = f"{info['width']}x{info['height']} | {dur:.1f}s | {meta.get('provider', '')}"

    draw.text((pad, 10), f"{title} ({year_str})", fill=(255, 224, 0), font=font_title)
    draw.text((pad, 38), f"Res: {res_str} | File: {video_path.name}", fill=(220, 226, 235), font=font_sub)
    draw.text((pad, 60), f"License: {lic_str}", fill=(140, 230, 175), font=font_sub)

    tmp_frames: List[Path] = []
    valid_frames = 0
    try:
        for idx, frac in enumerate(fracs):
            ts = max(0.0, min(max(0.0, dur - 0.15), dur * frac))
            frame_jpg = video_path.with_name(f".{video_path.stem}_frame_{idx}.jpg")
            tmp_frames.append(frame_jpg)
            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss", f"{ts:.2f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(frame_jpg),
            ]
            subprocess.run(cmd, capture_output=True, creationflags=_subprocess_flags())

            col = idx % cols
            row = idx // cols
            x0 = pad + col * (tile_w + pad)
            y0 = header_h + pad + row * (tile_h + pad)
            draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], fill=(10, 11, 14), outline=(70, 76, 88))

            if frame_jpg.is_file() and frame_jpg.stat().st_size > 0:
                try:
                    with Image.open(frame_jpg) as f_img:
                        rgb = ImageOps.exif_transpose(f_img).convert("RGB")
                        rgb.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
                        px = x0 + (tile_w - rgb.width) // 2
                        py = y0 + (tile_h - rgb.height) // 2
                        sheet.paste(rgb, (px, py))
                        valid_frames += 1
                except Exception:
                    pass

            mm = int(ts // 60)
            ss = ts - mm * 60
            label = f"{mm:02d}:{ss:04.1f} ({ts:.1f}s)"
            draw.rectangle([x0 + 6, y0 + tile_h - 30, x0 + 162, y0 + tile_h - 6], fill=(0, 0, 0))
            draw.text((x0 + 12, y0 + tile_h - 26), label, fill=(255, 235, 90), font=font_badge)

        if valid_frames == 0:
            out_jpg.unlink(missing_ok=True)
            return None

        sheet.save(out_jpg, format="JPEG", quality=92)
        norm_path, _, norm_err = _normalize_candidate_jpeg(out_jpg, max_long_edge=CANDIDATE_MAX_LONG_EDGE, quality=90)
        if norm_path is None:
            print(f"⚠️ Lỗi chuẩn hóa contact sheet '{out_jpg.name}': {norm_err}", file=sys.stderr)
            out_jpg.unlink(missing_ok=True)
            return None
        return norm_path
    except Exception as e:
        print(f"⚠️ Lỗi tạo contact sheet '{out_jpg.name}': {e}", file=sys.stderr)
        out_jpg.unlink(missing_ok=True)
        return None
    finally:
        for f in tmp_frames:
            f.unlink(missing_ok=True)


def run_mode_videos(args: argparse.Namespace) -> int:
    queries = list(args.query or [])
    if not queries:
        raise ValueError("Cần truyền ít nhất 1 --query khi dùng --mode videos.")

    out_dir = _resolve_candidates_dir(args, "videos")
    max_bytes = int(max(1, args.max_size_mb) * 1024 * 1024)
    session = _make_session()

    raw_items: List[Dict[str, Any]] = []
    if args.sources in ("all", "archive"):
        raw_items.extend(_search_internet_archive_videos(session, queries, limit=args.limit, max_bytes=max_bytes))
    if args.sources in ("all", "wikimedia"):
        wm_vids = _search_wikimedia(
            session,
            queries,
            filetype="video",
            limit=args.limit,
            exclude_modern=getattr(args, "exclude_modern", True),
        )
        for v in wm_vids:
            if v["ext"] in ALLOWED_VIDEO_EXTS and (v["size_bytes"] <= max_bytes or v["size_bytes"] == 0):
                raw_items.append(v)

    ffmpeg_bin = "" if args.dry_run else _find_media_binary("ffmpeg")
    ffprobe_bin = "" if args.dry_run else _find_media_binary("ffprobe")

    accepted: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    for item in raw_items:
        if len(accepted) >= args.limit:
            break
        idx = len(accepted) + 1
        ext = item["ext"] if item["ext"] in ALLOWED_VIDEO_EXTS else ".mp4"
        fname = f"vid_{idx:02d}_{_safe_slug(item.get('identifier') or item['title'])}{ext}"
        dest = out_dir / fname

        if args.dry_run:
            item["saved_file"] = ""
            item["contact_sheet"] = ""
            item["sha256"] = ""
            accepted.append(item)
            print(
                f"[DRY-RUN VID #{idx}] ({item['provider']}) {item['title'][:55]} | "
                f"năm={item.get('year_or_date') or '?'} | lic={item['license'][:45]}"
            )
            continue

        ok, err = _download_file(session, item["url"], dest, max_bytes=max_bytes)
        if not ok:
            print(f"   ⚠️ Lỗi tải video '{item['title']}': {err}")
            continue

        info = _probe_video(ffprobe_bin, dest)
        w, h = info.get("width", 0), info.get("height", 0)
        if not info.get("has_video") or min(w, h) < args.min_short_edge:
            print(f"   ⏭️ Bỏ qua {fname}: độ phân giải thấp ({w}x{h} < {args.min_short_edge}px)")
            dest.unlink(missing_ok=True)
            continue

        sha256 = _sha256_file(dest)
        if sha256 in seen_sha256:
            dest.unlink(missing_ok=True)
            continue
        seen_sha256.add(sha256)

        item["width"], item["height"] = w, h
        item["duration"] = round(float(info.get("duration") or 0.0), 2)
        item["sha256"] = sha256
        item["saved_file"] = fname

        cs_name = ""
        if not args.no_contact_sheet:
            cs_path = out_dir / f"{dest.stem}_contact.jpg"
            made = create_video_contact_sheet(ffmpeg_bin, ffprobe_bin, dest, cs_path, item)
            if made:
                cs_name = cs_path.name
        item["contact_sheet"] = cs_name
        accepted.append(item)
        print(f"✓ VID #{idx}: {fname} ({w}x{h}, {item['duration']}s) -> contact={cs_name or 'off'}")

    _write_candidates_manifest(out_dir, accepted, queries=queries, dry_run=args.dry_run, kind="videos")
    print(f"\n🎬 Đã lưu {len(accepted)} ứng viên video tại: {out_dir}")
    return 0


def _write_candidates_manifest(
    out_dir: Path,
    items: Sequence[Dict[str, Any]],
    *,
    queries: Sequence[str],
    dry_run: bool,
    kind: str,
) -> None:
    manifest = {
        "kind": kind,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "queries": list(queries),
        "count": len(items),
        "items": list(items),
    }
    (out_dir / "candidates.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Ứng viên {kind} tư liệu thật",
        f"- Queries: {', '.join(queries)}",
        f"- Tổng cộng: {len(items)}",
        "",
    ]
    for i, it in enumerate(items, 1):
        lines.append(f"## {i}. {it.get('title') or it.get('saved_file')}")
        lines.append(f"- File: `{it.get('saved_file') or '(dry-run)'}`")
        if it.get("contact_sheet"):
            lines.append(f"- Contact sheet: `{it['contact_sheet']}`")
        lines.append(f"- Nguồn: `{it.get('provider')}` | Kích thước: `{it.get('width')}x{it.get('height')}`")
        lines.append(f"- Tác giả: {it.get('author') or '?'} | Thời gian: {it.get('year_or_date') or '?'}")
        lines.append(f"- Giấy phép: {it.get('license') or '?'}")
        lines.append(f"- URL gốc: {it.get('page_url') or it.get('url')}")
        if it.get("description"):
            lines.append(f"- Mô tả gốc: {it['description']}")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ Mode 3 & 4: Commit into assets/
def _lookup_candidate_meta(src_file: Path) -> Dict[str, Any]:
    """Tự động nạp metadata ứng viên nếu file nằm cạnh candidates.json."""
    cand_json = src_file.parent / "candidates.json"
    if not cand_json.is_file():
        return {}
    try:
        data = json.loads(cand_json.read_text(encoding="utf-8"))
        items = data.get("items") or []
        for it in items:
            if it.get("saved_file") == src_file.name:
                return dict(it)
        for it in items:
            saved_name = str(it.get("saved_file") or "").strip()
            if saved_name and Path(saved_name).stem == src_file.stem:
                return dict(it)
    except Exception:
        pass
    return {}


def _update_archive_sources(episode_dir: Path, scene_id: int, entry: Dict[str, Any]) -> Path:
    sources_path = episode_dir / "archive_sources.json"
    payload: Dict[str, Any] = {"episode": episode_dir.name, "updated_at": "", "assets": {}}
    if sources_path.is_file():
        try:
            loaded = json.loads(sources_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload.update(loaded)
                if not isinstance(payload.get("assets"), dict):
                    payload["assets"] = {}
        except Exception:
            pass
    payload["episode"] = episode_dir.name
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["assets"][f"{scene_id:03d}"] = entry
    payload["assets"] = dict(sorted(payload["assets"].items(), key=lambda kv: kv[0]))
    sources_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sources_path


def _merge_source_meta(src_file: Path, args: argparse.Namespace) -> Dict[str, Any]:
    meta = _lookup_candidate_meta(src_file)
    if getattr(args, "title", None):
        meta["title"] = args.title
    if getattr(args, "author", None):
        meta["author"] = args.author
    if getattr(args, "license", None):
        meta["license"] = args.license
    if getattr(args, "source_url", None):
        meta["page_url"] = args.source_url
        meta["url"] = args.source_url
    return meta


def run_mode_commit_image(args: argparse.Namespace) -> int:
    src = Path(args.input).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh đầu vào: {src}")
    ep_dir = Path(args.episode_dir).resolve()
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_jpg = assets_dir / f"{args.scene:03d}.jpg"

    crop_margins_raw = str(getattr(args, "crop_margins", "") or "").strip()
    with Image.open(src) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        if crop_margins_raw:
            parts = [p.strip() for p in crop_margins_raw.split(",")]
            if len(parts) != 4:
                raise ValueError(f"--crop-margins phải có dạng L,T,R,B (nhận: '{crop_margins_raw}')")
            try:
                ml, mt, mr, mb = (int(v) for v in parts)
            except ValueError as exc:
                raise ValueError(f"--crop-margins phải là 4 số nguyên L,T,R,B (nhận: '{crop_margins_raw}')") from exc
            if min(ml, mt, mr, mb) < 0:
                raise ValueError(f"--crop-margins không chấp nhận giá trị âm: '{crop_margins_raw}'")
            w0, h0 = rgb.size
            if ml + mr >= w0 - 64 or mt + mb >= h0 - 64:
                raise ValueError(
                    f"--crop-margins={crop_margins_raw} vượt quá vùng hợp lệ của ảnh {w0}x{h0}"
                )
            rgb = rgb.crop((ml, mt, w0 - mr, h0 - mb))
        # Giữ nguyên tỉ lệ tự nhiên cho Ken Burns, chỉ thu gọn nếu vượt quá 3840px để tối ưu dung lượng
        if max(rgb.size) > 3840:
            rgb.thumbnail((3840, 3840), Image.Resampling.LANCZOS)
        w, h = rgb.size
        rgb.save(out_jpg, format="JPEG", quality=95, subsampling=0)

    meta = _merge_source_meta(src, args)
    entry = {
        "scene_id": args.scene,
        "asset_file": f"assets/{out_jpg.name}",
        "media_type": "image",
        "title": meta.get("title") or src.name,
        "provider": meta.get("provider") or "manual_archive",
        "author": meta.get("author") or "Unknown",
        "year_or_date": meta.get("year_or_date") or "",
        "license": meta.get("license") or "Verified Public Domain / Open License",
        "source_url": meta.get("page_url") or meta.get("url") or "",
        "crop_margins": crop_margins_raw,
        "resolution": f"{w}x{h}",
        "sha256": _sha256_file(out_jpg),
        "note": getattr(args, "note", "") or "",
        "committed_at": datetime.now().isoformat(timespec="seconds"),
    }
    sources_path = _update_archive_sources(ep_dir, args.scene, entry)
    print(f"✓ Đã nạp ảnh: {out_jpg} ({w}x{h}) | Nguồn đã lưu tại: {sources_path}")
    return 0


def _detect_black_bar_crop(
    ffmpeg_bin: str,
    src: Path,
    start_s: float,
    orig_w: int,
    orig_h: int,
) -> Optional[str]:
    """Dùng FFmpeg cropdetect phát hiện và loại bỏ viền đen quanh khung hình (nếu hợp lệ)."""
    if orig_w < 160 or orig_h < 160:
        return None
    cmd = [
        ffmpeg_bin,
        "-ss", f"{max(0.0, start_s):.3f}",
        "-t", "2.0",
        "-i", str(src),
        "-vf", "cropdetect=24:16:0",
        "-f", "null",
        "-",
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_subprocess_flags(),
    )
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", res.stderr)
    if not matches:
        return None
    cw, ch, cx, cy = (int(v) for v in matches[-1])
    cw -= cw % 2
    ch -= ch % 2
    if cw < 160 or ch < 160 or cw > orig_w or ch > orig_h:
        return None
    if cw == orig_w and ch == orig_h:
        return None
    # Chống cắt lố trên cảnh ban đêm: diện tích sau cắt phải giữ tối thiểu 45% khung gốc
    if (cw * ch) < 0.45 * (orig_w * orig_h):
        return None
    return f"crop={cw}:{ch}:{cx}:{cy}"


def run_mode_commit_video(args: argparse.Namespace) -> int:
    src = Path(args.input).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Không tìm thấy video đầu vào: {src}")
    ffmpeg_bin = _find_media_binary("ffmpeg")
    ffprobe_bin = _find_media_binary("ffprobe")

    in_info = _probe_video(ffprobe_bin, src)
    if not in_info.get("has_video"):
        raise RuntimeError(f"File không có luồng video hợp lệ: {src}")
    in_dur = float(in_info.get("duration") or 0.0)
    start_s = max(0.0, float(args.start))
    if in_dur > 0.0 and start_s >= in_dur - 0.2:
        raise ValueError(f"--start={start_s}s vượt quá thời lượng clip ({in_dur:.2f}s)")
    dur_s = max(0.5, float(args.duration))
    if in_dur > 0.0 and start_s + dur_s > in_dur:
        dur_s = max(0.5, in_dur - start_s)

    ep_dir = Path(args.episode_dir).resolve()
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = assets_dir / f"{args.scene:03d}.mp4"

    vf_parts: List[str] = []
    crop_expr = None
    if not args.no_crop_black_bars:
        crop_expr = _detect_black_bar_crop(ffmpeg_bin, src, start_s, in_info["width"], in_info["height"])
        if crop_expr:
            vf_parts.append(crop_expr)
    vf_parts.extend(["fps=30", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "setsar=1"])

    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", f"{start_s:.3f}",
        "-t", f"{dur_s:.3f}",
        "-i", str(src),
        "-an",
        "-vf", ",".join(vf_parts),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_subprocess_flags(),
    )
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg commit-video thất bại:\n{res.stderr.strip()[-800:]}")

    out_info = _probe_video(ffprobe_bin, out_mp4)
    if not out_info.get("has_video") or out_info.get("has_audio"):
        raise RuntimeError(f"Kiểm định đầu ra thất bại (has_video={out_info.get('has_video')}, has_audio={out_info.get('has_audio')})")

    meta = _merge_source_meta(src, args)
    entry = {
        "scene_id": args.scene,
        "asset_file": f"assets/{out_mp4.name}",
        "media_type": "video",
        "title": meta.get("title") or src.name,
        "provider": meta.get("provider") or "manual_archive",
        "author": meta.get("author") or "Unknown",
        "year_or_date": meta.get("year_or_date") or "",
        "license": meta.get("license") or "Verified Public Domain / Open License",
        "source_url": meta.get("page_url") or meta.get("url") or "",
        "trim_start_sec": round(start_s, 3),
        "trim_duration_sec": round(float(out_info.get("duration") or dur_s), 3),
        "crop_applied": crop_expr or "",
        "resolution": f"{out_info.get('width')}x{out_info.get('height')}",
        "muted": True,
        "sha256": _sha256_file(out_mp4),
        "note": getattr(args, "note", "") or "",
        "committed_at": datetime.now().isoformat(timespec="seconds"),
    }
    sources_path = _update_archive_sources(ep_dir, args.scene, entry)
    print(
        f"✓ Đã cắt & nạp video: {out_mp4} ({entry['resolution']}, {entry['trim_duration_sec']}s, -an mute)"
        + (f" [crop={crop_expr}]" if crop_expr else "")
        + f" | Nguồn: {sources_path}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Săn ảnh (Wikimedia Commons) & video (Internet Archive / Wikimedia) tư liệu lịch sử thật V5.2.",
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=["images", "videos", "commit-image", "commit-video"],
        help="Chế độ chạy: săn ảnh, săn video + contact sheet, hoặc chuẩn hóa nạp vào assets/.",
    )
    p.add_argument("--query", action="append", help="Từ khóa tìm kiếm (có thể truyền nhiều --query cho Việt/Anh/Pháp).")
    p.add_argument("--episode-dir", help="Đường dẫn thư mục tập (vd: story_mode/projects/.../Dien_Bien_Phu_Tap_04).")
    p.add_argument("--episode-name", help="Tên thư mục con lưu ứng viên trong automation/debug/archive_candidates/.")
    p.add_argument("--out-dir", help="Thư mục tùy chỉnh lưu ứng viên images/videos (ghi đè --episode-name).")
    p.add_argument("--limit", type=int, default=8, help="Số lượng ứng viên tối đa cần tải (mặc định: 8).")
    p.add_argument("--min-long-edge", type=int, default=800, help="Cạnh dài tối thiểu cho ảnh (mặc định: 800px).")
    p.add_argument("--min-short-edge", type=int, default=360, help="Cạnh ngắn tối thiểu cho video (mặc định: 360px).")
    p.add_argument("--max-size-mb", type=int, default=250, help="Dung lượng file video tối đa cho phép tải (mặc định: 250MB).")
    p.add_argument("--sources", choices=["all", "archive", "wikimedia"], default="all", help="Nguồn video: all / archive / wikimedia.")
    p.add_argument("--no-contact-sheet", action="store_true", help="Tắt tự động tạo lưới ảnh xem trước 6 khung hình.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ truy vấn metadata kiểm tra nguồn & giấy phép, không tải file media.")
    mod_group = p.add_mutually_exclusive_group()
    mod_group.add_argument(
        "--exclude-modern",
        dest="exclude_modern",
        action="store_true",
        help="Lọc bỏ ảnh du lịch/tượng đài/di tích chụp hiện đại (mặc định bật cho sử cận/hiện đại; vẫn giữ ảnh lưu trữ gốc).",
    )
    mod_group.add_argument(
        "--include-sites",
        dest="exclude_modern",
        action="store_false",
        help="Tắt lọc ảnh di tích/bảo tàng hiện đại (dùng khi làm chủ đề sử cổ đại, di tích, hiện vật).",
    )
    p.set_defaults(exclude_modern=True)

    # Tham số commit-image & commit-video
    p.add_argument("--input", help="Đường dẫn ảnh hoặc video ứng viên đã duyệt.")
    p.add_argument("--scene", type=int, help="Số thứ tự cảnh đích (1 -> assets/001.jpg hoặc assets/001.mp4).")
    p.add_argument("--start", type=float, default=0.0, help="Mốc bắt đầu cắt video theo giây (mặc định: 0.0).")
    p.add_argument("--duration", type=float, default=6.0, help="Độ dài đoạn video cần cắt theo giây (mặc định: 6.0).")
    p.add_argument("--no-crop-black-bars", action="store_true", help="Tắt tự động dò cắt viền đen ở chế độ commit-video.")
    p.add_argument("--crop-margins", help="Cắt lề ảnh theo pixel dạng L,T,R,B trước khi lưu commit-image (vd: cắt viền trắng bản scan).")
    p.add_argument("--source-url", help="Ghi đè URL nguồn tư liệu trong archive_sources.json.")
    p.add_argument("--license", help="Ghi đè tên giấy phép trong archive_sources.json.")
    p.add_argument("--author", help="Ghi đè tên tác giả/nhà lưu trữ trong archive_sources.json.")
    p.add_argument("--title", help="Ghi đè tiêu đề tư liệu trong archive_sources.json.")
    p.add_argument("--note", help="Ghi chú kiểm duyệt thời kỳ/bối cảnh khi lưu archive_sources.json.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "images":
        return run_mode_images(args)
    if args.mode == "videos":
        return run_mode_videos(args)
    if args.mode == "commit-image":
        if not args.input or not args.episode_dir or args.scene is None:
            raise ValueError("--mode commit-image yêu cầu --input, --episode-dir và --scene.")
        return run_mode_commit_image(args)
    if args.mode == "commit-video":
        if not args.input or not args.episode_dir or args.scene is None:
            raise ValueError("--mode commit-video yêu cầu --input, --episode-dir và --scene.")
        return run_mode_commit_video(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
