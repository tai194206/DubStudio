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
NARA_API_URL = "https://catalog.archives.gov/api/v1/"
DDG_HTML_SEARCH_URL = "https://html.duckduckgo.com/html/"

DEFAULT_SOURCES = "wikimedia,internet_archive,nara"
EDITORIAL_PRESS_LICENSE = "Editorial / Press Archive (Credit Required)"
VN_PRESS_MIN_EDGE = 600

VN_PRESS_DOMAINS: Dict[str, str] = {
    "qdnd.vn": "Báo Quân đội nhân dân (qdnd.vn)",
    "baotanglichsu.vn": "Bảo tàng Lịch sử Quốc gia (baotanglichsu.vn)",
    "btlsqsvn.org.vn": "Bảo tàng Lịch sử Quân sự Việt Nam (btlsqsvn.org.vn)",
    "vnanet.vn": "Thông tấn xã Việt Nam (vnanet.vn)",
    "nhandan.vn": "Báo Nhân Dân (nhandan.vn)",
    "vietnamplus.vn": "VietnamPlus - TTXVN (vietnamplus.vn)",
    "baochinhphu.vn": "Báo Điện tử Chính phủ (baochinhphu.vn)",
    "cand.com.vn": "Báo Công an nhân dân (cand.com.vn)",
}

_NON_CONTENT_IMG_RE = re.compile(
    r"(?:(?:^|[/_.\-])(?:logo|avatar|icon|favicon|banner|ads?|advert|share|button|sprite|badge|arrow|bullet|spinner|loading|placeholder|blank|pixel|spacer)(?:[/_.\-]|$)|"
    r"\.(?:svg|gif)(?:\?|$)|^data:image/)",
    re.IGNORECASE,
)


def parse_sources_arg(raw_sources: Any) -> List[str]:
    """Chuẩn hóa danh sách nguồn từ chuỗi phân tách dấu phẩy hoặc list."""
    if raw_sources is None:
        raw_tokens = DEFAULT_SOURCES.split(",")
    elif isinstance(raw_sources, (list, tuple, set)):
        raw_tokens = []
        for item in raw_sources:
            raw_tokens.extend(str(item).split(","))
    else:
        raw_tokens = str(raw_sources).split(",")

    normalized: List[str] = []
    alias_map = {
        "wikimedia": "wikimedia",
        "wikimedia_commons": "wikimedia",
        "commons": "wikimedia",
        "internet_archive": "internet_archive",
        "archive": "internet_archive",
        "archive.org": "internet_archive",
        "ia": "internet_archive",
        "nara": "nara",
        "national_archives": "nara",
        "vn_press": "vn_press",
        "vnpress": "vn_press",
        "press": "vn_press",
    }
    for tok in raw_tokens:
        clean = tok.strip().lower()
        if not clean:
            continue
        if clean == "all":
            for s in ("wikimedia", "internet_archive", "nara", "vn_press"):
                if s not in normalized:
                    normalized.append(s)
            continue
        mapped = alias_map.get(clean)
        if not mapped:
            raise ValueError(
                f"Nguồn '--sources' không hợp lệ: '{tok}' (hỗ trợ: wikimedia, internet_archive, nara, vn_press, all)."
            )
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized or ["wikimedia", "internet_archive", "nara"]


def resolve_vn_press_publisher(url: str) -> str:
    """Nhận diện tên cơ quan báo chí / bảo tàng từ URL bài viết."""
    host = urllib.parse.urlparse(str(url or "").strip()).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    for dom, label in VN_PRESS_DOMAINS.items():
        if host == dom or host.endswith("." + dom):
            return label
    return host or "Báo chí / Bảo tàng Việt Nam"

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


# ------------------------------------------------------------------ Internet Archive Images & NARA Images
def _pick_best_ia_image_file(files: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for f in files:
        name = str(f.get("name") or "")
        low = name.lower()
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            continue
        if any(skip in low for skip in ("_thumb", "__ia_thumb", "_itemimage", "logo", "icon")):
            continue
        sz = _safe_int(f.get("size"))
        if sz <= 0 or sz > 35 * 1024 * 1024:
            continue
        score = 0
        if ext in (".jpg", ".jpeg"):
            score += 100
        elif ext == ".png":
            score += 80
        if str(f.get("source") or "").lower() == "original":
            score += 30
        w = _safe_int(f.get("width"))
        h = _safe_int(f.get("height"))
        if max(w, h) >= 800:
            score += 25
        scored.append((score, f))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def _search_internet_archive_images(
    session: requests.Session,
    queries: Sequence[str],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Tìm ảnh tư liệu lịch sử Public Domain trên Internet Archive (archive.org)."""
    candidates: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    rows = max(limit * 2, 10)

    for q in queries:
        q_clean = str(q).strip()
        if not q_clean:
            continue
        params = [
            ("q", f"({q_clean}) AND mediatype:(image)"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "year"),
            ("fl[]", "date"),
            ("fl[]", "description"),
            ("fl[]", "creator"),
            ("fl[]", "licenseurl"),
            ("fl[]", "rights"),
            ("rows", str(min(30, rows))),
            ("page", "1"),
            ("output", "json"),
        ]
        try:
            resp = session.get(IA_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            docs = ((resp.json().get("response") or {}).get("docs") or [])
        except Exception as e:
            print(f"⚠️ Lỗi tìm ảnh Internet Archive cho '{q_clean}': {e}", file=sys.stderr)
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
                continue

            try:
                meta_resp = session.get(IA_METADATA_URL.format(identifier=ident), timeout=REQUEST_TIMEOUT)
                meta_resp.raise_for_status()
                meta_json = meta_resp.json()
            except Exception:
                continue

            files = meta_json.get("files") or []
            best_file = _pick_best_ia_image_file(files)
            if not best_file:
                continue

            fname = str(best_file.get("name") or "")
            ext = Path(fname).suffix.lower() or ".jpg"
            dl_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}"
            lic_label = lic_url or rights or "Public Domain / Internet Archive"
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
                    "mime": "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}",
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


def _extract_nara_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Hỗ trợ đọc danh sách kết quả từ cấu trúc JSON NARA API v1/v2."""
    if not isinstance(payload, dict):
        return []
    opa = payload.get("opaResponse")
    if isinstance(opa, dict):
        res_obj = opa.get("results")
        if isinstance(res_obj, dict):
            arr = res_obj.get("result")
            if isinstance(arr, list):
                return [r for r in arr if isinstance(r, dict)]
            if isinstance(arr, dict):
                return [arr]
    for key in ("results", "data", "hits", "records", "items"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            for sub_k in ("result", "hits", "items", "records"):
                sub_v = val.get(sub_k)
                if isinstance(sub_v, list):
                    return [r for r in sub_v if isinstance(r, dict)]
    return []


def _extract_nara_image_url_and_meta(rec: Dict[str, Any]) -> Optional[Tuple[str, str, str, str, str]]:
    """Trích (image_url, title, date_str, naid, desc) từ một bản ghi NARA."""
    naid = str(rec.get("naId") or rec.get("naid") or rec.get("id") or "").strip()
    title = str(rec.get("title") or "").strip()
    date_str = str(rec.get("year") or rec.get("date") or "").strip()
    desc = str(rec.get("description") or "").strip()

    desc_block = rec.get("description")
    if isinstance(desc_block, dict):
        item_blk = desc_block.get("item") or desc_block.get("series") or desc_block
        if isinstance(item_blk, dict):
            title = title or str(item_blk.get("title") or "").strip()
            naid = naid or str(item_blk.get("naId") or "").strip()
            desc = str(item_blk.get("scopeAndContentNote") or "").strip()
            prod_dates = item_blk.get("productionDateArray")
            if isinstance(prod_dates, dict):
                prod_dates = [prod_dates]
            if isinstance(prod_dates, list) and prod_dates:
                first_d = prod_dates[0]
                if isinstance(first_d, dict):
                    logical_d = first_d.get("proposableQualifiableDate") or first_d
                    if isinstance(logical_d, dict):
                        date_str = date_str or str(logical_d.get("logicalDate") or logical_d.get("year") or "").strip()

    # Tìm URL ảnh từ objects.object hoặc digitalObjects hoặc url trực tiếp
    candidate_urls: List[str] = []
    for direct_k in ("image_url", "imageUrl", "objectUrl", "url"):
        v = str(rec.get(direct_k) or "").strip()
        if v and Path(urllib.parse.urlparse(v).path).suffix.lower() in ALLOWED_IMAGE_EXTS:
            candidate_urls.append(v)

    objs_root = rec.get("objects") or rec.get("digitalObjects")
    if isinstance(objs_root, dict):
        obj_list = objs_root.get("object") or objs_root.get("objects") or objs_root.get("digitalObject") or []
    elif isinstance(objs_root, list):
        obj_list = objs_root
    else:
        obj_list = []
    if isinstance(obj_list, dict):
        obj_list = [obj_list]

    for obj in obj_list:
        if not isinstance(obj, dict):
            continue
        file_info = obj.get("file") or obj
        if isinstance(file_info, dict):
            for uk in ("@url", "url", "path"):
                u = str(file_info.get(uk) or "").strip()
                if u and Path(urllib.parse.urlparse(u).path).suffix.lower() in ALLOWED_IMAGE_EXTS:
                    candidate_urls.append(u)

    if not candidate_urls:
        return None
    return candidate_urls[0], (title or f"NARA_{naid or 'Archive'}"), date_str, naid, _strip_html(desc)


def _search_nara_images(
    session: requests.Session,
    queries: Sequence[str],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Truy vấn API công khai của US National Archives (catalog.archives.gov) lấy ảnh quân sự Public Domain."""
    collected: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    rows = min(30, max(limit * 2, 10))

    for q in queries:
        q_clean = str(q).strip()
        if not q_clean:
            continue
        params = {
            "q": q_clean,
            "resultTypes": "item",
            "rows": str(rows),
        }
        try:
            resp = session.get(NARA_API_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"⚠️ Lỗi truy vấn NARA cho '{q_clean}': {e}", file=sys.stderr)
            continue

        for rec in _extract_nara_records(payload):
            extracted = _extract_nara_image_url_and_meta(rec)
            if not extracted:
                continue
            img_url, title, date_str, naid, desc = extracted
            if img_url in seen_urls or FORBIDDEN_MEDIA_RE.search(f"{title} {desc}"):
                continue
            seen_urls.add(img_url)
            ext = Path(urllib.parse.urlparse(img_url).path).suffix.lower() or ".jpg"
            page_url = f"https://catalog.archives.gov/id/{naid}" if naid else img_url
            collected.append(
                {
                    "provider": "nara",
                    "query": q_clean,
                    "title": title[:180],
                    "author": "U.S. National Archives (NARA)",
                    "year_or_date": date_str[:80],
                    "description": desc[:400],
                    "license": "Public Domain — U.S. National Archives",
                    "mime": "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}",
                    "ext": ext,
                    "width": _safe_int(rec.get("width")) or 1200,
                    "height": _safe_int(rec.get("height")) or 900,
                    "size_bytes": 0,
                    "url": img_url,
                    "page_url": page_url,
                }
            )
            if len(collected) >= limit * 2:
                break
    return collected


# ------------------------------------------------------------------ Mode 1: Images
def run_mode_images(args: argparse.Namespace) -> int:
    queries = list(args.query or [])
    sources = parse_sources_arg(getattr(args, "sources", DEFAULT_SOURCES))
    if not queries and not getattr(args, "urls", None):
        raise ValueError("Cần truyền ít nhất 1 --query (hoặc --urls cho báo chí VN).")

    if getattr(args, "urls", None) or (sources == ["vn_press"]):
        return run_mode_vn_press(args)

    out_dir = _resolve_candidates_dir(args, "images")
    session = _make_session()
    raw_items: List[Dict[str, Any]] = []

    if "wikimedia" in sources and queries:
        raw_items.extend(
            _search_wikimedia(
                session,
                queries,
                filetype="bitmap",
                limit=args.limit,
                exclude_modern=getattr(args, "exclude_modern", True),
            )
        )
    if "internet_archive" in sources and queries:
        raw_items.extend(_search_internet_archive_images(session, queries, limit=args.limit))
    if "nara" in sources and queries:
        raw_items.extend(_search_nara_images(session, queries, limit=args.limit))

    accepted: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    for item in raw_items:
        if len(accepted) >= args.limit:
            break
        ext = item["ext"]
        mime = item.get("mime", "")
        long_edge = max(int(item.get("width") or 0), int(item.get("height") or 0))
        if ext not in ALLOWED_IMAGE_EXTS or (mime and mime not in ALLOWED_IMAGE_MIMES):
            continue
        if long_edge > 0 and long_edge < args.min_long_edge:
            continue

        idx = len(accepted) + 1
        fname = f"cand_{idx:02d}_{_safe_slug(item['title'])}{ext}"
        dest = out_dir / fname

        if args.dry_run:
            item["saved_file"] = ""
            item["sha256"] = ""
            accepted.append(item)
            print(f"[DRY-RUN IMG #{idx}] ({item.get('provider')}) {item['width']}x{item['height']} | {item['license']} | {item['title']}")
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

    if "vn_press" in sources and queries:
        run_mode_vn_press(args)
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
    sources = parse_sources_arg(getattr(args, "sources", DEFAULT_SOURCES))

    raw_items: List[Dict[str, Any]] = []
    if "internet_archive" in sources:
        raw_items.extend(_search_internet_archive_videos(session, queries, limit=args.limit, max_bytes=max_bytes))
    if "wikimedia" in sources:
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


def _resolve_commit_assets_dir(ep_dir: Path, args: argparse.Namespace) -> Path:
    raw_dir = getattr(args, "assets_dir", None)
    if raw_dir:
        p = Path(raw_dir)
        return p.resolve() if p.is_absolute() else (ep_dir / p).resolve()
    if getattr(args, "youtube_16x9", False):
        return (ep_dir / "assets_16x9").resolve()
    return (ep_dir / "assets").resolve()


def _parse_target_size(args: argparse.Namespace) -> Optional[Tuple[int, int]]:
    raw = str(getattr(args, "target_size", "") or "").strip().lower()
    if raw:
        m = re.fullmatch(r"(\d{2,5})\s*[x×:,]\s*(\d{2,5})", raw)
        if not m:
            raise ValueError(f"--target-size phải có dạng WxH (vd: 1920x1080), nhận: '{raw}'")
        w, h = int(m.group(1)), int(m.group(2))
        if w < 64 or h < 64:
            raise ValueError(f"--target-size quá nhỏ: {w}x{h}")
        return w - (w % 2), h - (h % 2)
    if getattr(args, "youtube_16x9", False):
        return (1920, 1080)
    return None


def _update_archive_sources(
    episode_dir: Path,
    scene_id: int,
    entry: Dict[str, Any],
    *,
    is_parallel_16x9: bool = False,
) -> Path:
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
    key = f"{scene_id:03d}"
    payload["episode"] = episode_dir.name
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")

    existing = payload["assets"].get(key)
    if is_parallel_16x9 and isinstance(existing, dict):
        merged = dict(existing)
        merged["asset_file_16x9"] = entry.get("asset_file", "")
        merged["resolution_16x9"] = entry.get("resolution", "")
        merged["sha256_16x9"] = entry.get("sha256", "")
        merged["updated_16x9_at"] = entry.get("committed_at", "")
        for k in ("title", "provider", "author", "year_or_date", "license", "source_url"):
            if not merged.get(k) and entry.get(k):
                merged[k] = entry[k]
        payload["assets"][key] = merged
    else:
        payload["assets"][key] = entry

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
    assets_dir = _resolve_commit_assets_dir(ep_dir, args)
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_jpg = assets_dir / f"{args.scene:03d}.jpg"
    target_size = _parse_target_size(args)

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

        if target_size is not None:
            rgb = ImageOps.fit(rgb, target_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.35))
        elif max(rgb.size) > 3840:
            # Giữ nguyên tỉ lệ tự nhiên cho Ken Burns, chỉ thu gọn nếu vượt quá 3840px để tối ưu dung lượng
            rgb.thumbnail((3840, 3840), Image.Resampling.LANCZOS)
        w, h = rgb.size
        rgb.save(out_jpg, format="JPEG", quality=95, subsampling=0)

    is_parallel = assets_dir.name != "assets"
    meta = _merge_source_meta(src, args)
    entry = {
        "scene_id": args.scene,
        "asset_file": f"{assets_dir.name}/{out_jpg.name}",
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
    sources_path = _update_archive_sources(ep_dir, args.scene, entry, is_parallel_16x9=is_parallel)
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
    assets_dir = _resolve_commit_assets_dir(ep_dir, args)
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = assets_dir / f"{args.scene:03d}.mp4"
    target_size = _parse_target_size(args)

    vf_parts: List[str] = []
    crop_expr = None
    if not args.no_crop_black_bars:
        crop_expr = _detect_black_bar_crop(ffmpeg_bin, src, start_s, in_info["width"], in_info["height"])
        if crop_expr:
            vf_parts.append(crop_expr)
    if target_size is not None:
        tw, th = target_size
        vf_parts.extend([
            "fps=30",
            f"scale={tw}:{th}:force_original_aspect_ratio=increase",
            f"crop={tw}:{th}:(iw-{tw})/2:(ih-{th})*0.35",
            "setsar=1",
        ])
    else:
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

    is_parallel = assets_dir.name != "assets"
    meta = _merge_source_meta(src, args)
    entry = {
        "scene_id": args.scene,
        "asset_file": f"{assets_dir.name}/{out_mp4.name}",
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
    sources_path = _update_archive_sources(ep_dir, args.scene, entry, is_parallel_16x9=is_parallel)
    print(
        f"✓ Đã cắt & nạp video: {out_mp4} ({entry['resolution']}, {entry['trim_duration_sec']}s, -an mute)"
        + (f" [crop={crop_expr}]" if crop_expr else "")
        + f" | Nguồn: {sources_path}"
    )
    return 0


# ------------------------------------------------------------------ Mode 5 & 6: VN Press & Museum Staging + Promote
def _extract_attr(tag_html: str, attr_name: str) -> str:
    m = re.search(
        rf"""\b{re.escape(attr_name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""",
        tag_html,
        re.IGNORECASE,
    )
    if not m:
        return ""
    raw = m.group(1) or m.group(2) or m.group(3) or ""
    return html.unescape(raw).strip()


def _pick_img_src_from_tag(img_tag: str, article_url: str) -> Tuple[str, str, int, int]:
    """Lấy (full_url, alt_or_title, html_w, html_h) từ thẻ <img ...>."""
    raw_src = ""
    for attr in ("data-original", "data-src", "data-lazy-src", "src"):
        cand = _extract_attr(img_tag, attr)
        if cand and not cand.lower().startswith("data:image/"):
            raw_src = cand
            break
    if not raw_src:
        return "", "", 0, 0
    full_url = urllib.parse.urljoin(article_url, raw_src)
    alt_txt = _strip_html(_extract_attr(img_tag, "alt") or _extract_attr(img_tag, "title"))
    hw = _safe_int(re.sub(r"[^\d]", "", _extract_attr(img_tag, "width")))
    hh = _safe_int(re.sub(r"[^\d]", "", _extract_attr(img_tag, "height")))
    return full_url, alt_txt, hw, hh


def _is_valid_press_img_candidate(full_url: str, img_tag: str, hw: int, hh: int) -> bool:
    if not full_url or _NON_CONTENT_IMG_RE.search(full_url):
        return False
    cls_id = f"{_extract_attr(img_tag, 'class')} {_extract_attr(img_tag, 'id')}".lower()
    if any(tok in cls_id for tok in ("logo", "avatar", "icon", "banner", "share", "ads", "author-img")):
        return False
    # Nếu HTML ghi rõ cả width và height mà cạnh dài < 600px (hoặc cạnh ngắn < 120px) thì lọc bỏ sớm
    if hw > 0 and hh > 0 and (max(hw, hh) < VN_PRESS_MIN_EDGE or min(hw, hh) < 120):
        return False
    if (hw > 0 and hw < 150) or (hh > 0 and hh < 150):
        return False
    return True


def extract_vn_press_images_from_html(html_text: str, article_url: str) -> List[Dict[str, Any]]:
    """Bóc tách danh sách ảnh trong bài báo/bảo tàng VN kèm chú thích (figcaption / td.caption / alt) và tên báo."""
    if not html_text:
        return []
    publisher = resolve_vn_press_publisher(article_url)
    cleaned_html = re.sub(r"<!--.*?-->", " ", html_text, flags=re.DOTALL)
    cleaned_html = re.sub(r"<(?:script|style|nav|header|footer|aside)\b[^>]*>.*?</(?:script|style|nav|header|footer|aside)>", " ", cleaned_html, flags=re.DOTALL | re.IGNORECASE)

    results_with_pos: List[Tuple[int, Dict[str, Any]]] = []
    seen_urls: set[str] = set()

    def _add_candidate(pos: int, img_tag: str, caption_raw: str) -> None:
        full_url, alt_txt, hw, hh = _pick_img_src_from_tag(img_tag, article_url)
        if not full_url or full_url in seen_urls:
            return
        if not _is_valid_press_img_candidate(full_url, img_tag, hw, hh):
            return
        caption = _strip_html(caption_raw) or alt_txt
        if FORBIDDEN_MEDIA_RE.search(f"{full_url} {caption}"):
            return
        seen_urls.add(full_url)
        results_with_pos.append(
            (
                pos,
                {
                    "image_url": full_url,
                    "caption": caption[:360],
                    "article_url": article_url,
                    "publisher": publisher,
                    "html_width": hw,
                    "html_height": hh,
                },
            )
        )

    # 1. Khối <figure ...> ... <img ...> ... <figcaption ...>...</figcaption> </figure>
    for fig_m in re.finditer(r"<figure\b[^>]*>(.*?)</figure>", cleaned_html, flags=re.DOTALL | re.IGNORECASE):
        block = fig_m.group(1)
        img_m = re.search(r"<img\b[^>]*>", block, flags=re.IGNORECASE)
        if not img_m:
            continue
        cap_m = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", block, flags=re.DOTALL | re.IGNORECASE)
        if not cap_m:
            cap_m = re.search(
                r"""<(?:p|div|span|em)\b[^>]*(?:class|id)\s*=\s*["'][^"']*(?:caption|desc|chuthich|note)[^"']*["'][^>]*>(.*?)</(?:p|div|span|em)>""",
                block,
                flags=re.DOTALL | re.IGNORECASE,
            )
        _add_candidate(fig_m.start() + img_m.start(), img_m.group(0), cap_m.group(1) if cap_m else "")

    # 2. Khối <table ...> chứa <img> và ô chú thích (chuẩn qdnd.vn / báo điện tử VN)
    for tbl_m in re.finditer(r"<table\b[^>]*>(.*?)</table>", cleaned_html, flags=re.DOTALL | re.IGNORECASE):
        block = tbl_m.group(1)
        img_m = re.search(r"<img\b[^>]*>", block, flags=re.IGNORECASE)
        if not img_m:
            continue
        cap_m = re.search(
            r"""<td\b[^>]*(?:class\s*=\s*["'][^"']*(?:caption|img|desc|chuthich|note)[^"']*["'])[^>]*>(.*?)</td>""",
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not cap_m:
            tds = re.findall(r"<td\b[^>]*>(.*?)</td>", block, flags=re.DOTALL | re.IGNORECASE)
            non_img_tds = [_strip_html(t) for t in tds if "<img" not in t.lower() and _strip_html(t)]
            cap_text = non_img_tds[0] if non_img_tds else ""
        else:
            cap_text = cap_m.group(1)
        _add_candidate(tbl_m.start() + img_m.start(), img_m.group(0), cap_text)

    # 3. Các thẻ <img> còn lại kèm đoạn chú thích ngay kế sau (hoặc alt/title)
    img_with_next_re = re.compile(
        r"(<img\b[^>]*>)(?:\s*</(?:a|p|div|span)>)*\s*"
        r"(?:<(?:figcaption|em)\b[^>]*>(.*?)</(?:figcaption|em)>|"
        r"""<(?:p|div|span)\b[^>]*(?:class|id)\s*=\s*["'][^"']*(?:caption|img-cap|photo-cap|chuthich|desc)[^"']*["'][^>]*>(.*?)</(?:p|div|span)>)?""",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for m in img_with_next_re.finditer(cleaned_html):
        img_tag = m.group(1)
        cap_raw = m.group(2) or m.group(3) or ""
        _add_candidate(m.start(), img_tag, cap_raw)

    results_with_pos.sort(key=lambda pair: pair[0])
    return [item for _, item in results_with_pos]


def _discover_vn_press_article_urls(
    session: requests.Session,
    queries: Sequence[str],
    *,
    limit: int = 6,
) -> List[str]:
    """Tìm bài viết tư liệu lịch sử trên các tên miền báo chí / bảo tàng VN uy tín theo từ khóa."""
    discovered: List[str] = []
    seen: set[str] = set()
    site_clause = " OR ".join(f"site:{dom}" for dom in list(VN_PRESS_DOMAINS.keys())[:6])

    for q in queries:
        q_clean = str(q).strip()
        if not q_clean:
            continue
        full_q = f"{q_clean} ({site_clause})"
        try:
            resp = session.get(DDG_HTML_SEARCH_URL, params={"q": full_q}, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            html_body = resp.text
        except Exception:
            continue

        # Bắt cả URL trực tiếp lẫn tham số uddg=... trong kết quả tìm kiếm
        for raw_href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", html_body, flags=re.IGNORECASE):
            cand_url = html.unescape(raw_href)
            if "uddg=" in cand_url:
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(cand_url).query)
                if parsed_qs.get("uddg"):
                    cand_url = parsed_qs["uddg"][0]
            host = re.sub(r"^www\.", "", urllib.parse.urlparse(cand_url).netloc.lower())
            if any(host == dom or host.endswith("." + dom) for dom in VN_PRESS_DOMAINS):
                if cand_url not in seen:
                    seen.add(cand_url)
                    discovered.append(cand_url)
                    if len(discovered) >= limit:
                        return discovered
    return discovered


def create_press_contact_sheet(
    staging_dir: Path,
    items: Sequence[Dict[str, Any]],
    out_path: Optional[Path] = None,
) -> Optional[Path]:
    """Tạo ảnh tổng hợp đánh số `staging_press/contact_sheet.jpg` để duyệt trực quan trước khi --promote."""
    dest_jpg = out_path or (staging_dir / "contact_sheet.jpg")
    valid_items = [it for it in items if it.get("filename") and (staging_dir / str(it["filename"])).is_file()]
    if not valid_items:
        return None

    cols = min(3, max(1, len(valid_items)))
    rows = (len(valid_items) + cols - 1) // cols
    tile_w, tile_h = 420, 310
    img_box_h = 216
    pad = 14
    header_h = 68

    canvas_w = cols * tile_w + (cols + 1) * pad
    canvas_h = header_h + rows * tile_h + (rows + 1) * pad
    sheet = Image.new("RGB", (canvas_w, canvas_h), (20, 22, 28))
    draw = ImageDraw.Draw(sheet)

    font_header = _load_pil_font(20)
    font_badge = _load_pil_font(16)
    font_meta = _load_pil_font(14)

    draw.text(
        (pad, 14),
        f"VN PRESS / MUSEUM STAGING CONTACT SHEET ({len(valid_items)} ảnh >= {VN_PRESS_MIN_EDGE}px)",
        fill=(255, 224, 0),
        font=font_header,
    )
    draw.text(
        (pad, 40),
        "Dùng lệnh: --promote <id>:<scene_num> (vd: --promote 1:3) để nạp ảnh đã chọn vào assets/",
        fill=(195, 205, 218),
        font=font_meta,
    )

    for idx, it in enumerate(valid_items):
        col = idx % cols
        row = idx // cols
        x0 = pad + col * (tile_w + pad)
        y0 = header_h + pad + row * (tile_h + pad)
        draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], fill=(12, 14, 18), outline=(72, 78, 92))

        img_file = staging_dir / str(it["filename"])
        try:
            with Image.open(img_file) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                rgb.thumbnail((tile_w - 8, img_box_h - 8), Image.Resampling.LANCZOS)
                px = x0 + (tile_w - rgb.width) // 2
                py = y0 + 4 + (img_box_h - rgb.height) // 2
                sheet.paste(rgb, (px, py))
        except Exception:
            pass

        item_id = it.get("id", idx + 1)
        res_txt = f"#{item_id} | {it.get('width', 0)}x{it.get('height', 0)}"
        draw.rectangle([x0 + 6, y0 + 6, x0 + 150, y0 + 32], fill=(0, 0, 0))
        draw.text((x0 + 12, y0 + 9), res_txt, fill=(255, 230, 80), font=font_badge)

        pub_txt = str(it.get("publisher") or "")[:48]
        cap_txt = str(it.get("caption") or it.get("filename") or "")[:54]
        draw.text((x0 + 10, y0 + img_box_h + 10), pub_txt, fill=(140, 230, 175), font=font_meta)
        draw.text((x0 + 10, y0 + img_box_h + 34), cap_txt, fill=(230, 234, 240), font=font_meta)
        draw.text((x0 + 10, y0 + img_box_h + 56), str(it.get("filename") or "")[:48], fill=(155, 162, 175), font=font_meta)

    dest_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest_jpg, format="JPEG", quality=92)
    return dest_jpg


def _resolve_staging_press_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "episode_dir", None):
        base = Path(args.episode_dir).resolve()
    elif getattr(args, "out_dir", None):
        base = Path(args.out_dir).resolve()
    elif getattr(args, "episode_name", None):
        base = (DEFAULT_CANDIDATES_ROOT / _safe_slug(args.episode_name, max_len=64)).resolve()
    else:
        base = (DEFAULT_CANDIDATES_ROOT / "default_episode").resolve()
    staging_dir = base if base.name == "staging_press" else (base / "staging_press")
    staging_dir.mkdir(parents=True, exist_ok=True)
    return staging_dir


def stage_vn_press_images(
    session: requests.Session,
    article_urls: Sequence[str],
    *,
    project_dir: Path,
    min_long_edge: int = VN_PRESS_MIN_EDGE,
    limit: int = 24,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Tải HTML bài báo/bảo tàng VN, lọc ảnh thực >= 600px vào `<project_dir>/staging_press/`, tạo manifest + contact sheet."""
    staging_dir = project_dir if project_dir.name == "staging_press" else (project_dir / "staging_press")
    staging_dir.mkdir(parents=True, exist_ok=True)
    eff_min_edge = max(VN_PRESS_MIN_EDGE, int(min_long_edge or VN_PRESS_MIN_EDGE))

    extracted_candidates: List[Dict[str, Any]] = []
    seen_img_urls: set[str] = set()

    for art_url in article_urls:
        u = str(art_url or "").strip()
        if not u:
            continue
        try:
            resp = session.get(u, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            html_text = resp.text
        except Exception as e:
            print(f"⚠️ Không thể tải bài viết '{u}': {e}", file=sys.stderr)
            continue

        for img_cand in extract_vn_press_images_from_html(html_text, u):
            img_u = img_cand["image_url"]
            if img_u in seen_img_urls:
                continue
            seen_img_urls.add(img_u)
            extracted_candidates.append(img_cand)

    manifest_items: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    for cand in extracted_candidates:
        if len(manifest_items) >= limit:
            break
        item_id = len(manifest_items) + 1
        img_url = cand["image_url"]
        slug_base = cand.get("caption") or Path(urllib.parse.urlparse(img_url).path).stem or f"press_{item_id:02d}"
        ext = Path(urllib.parse.urlparse(img_url).path).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            ext = ".jpg"

        if dry_run:
            manifest_items.append(
                {
                    "id": item_id,
                    "filename": f"press_{item_id:02d}_{_safe_slug(slug_base, max_len=42)}.jpg",
                    "width": int(cand.get("html_width") or 0),
                    "height": int(cand.get("html_height") or 0),
                    "caption": cand.get("caption") or "",
                    "article_url": cand.get("article_url") or "",
                    "publisher": cand.get("publisher") or "",
                    "image_url": img_url,
                    "license": EDITORIAL_PRESS_LICENSE,
                    "sha256": "",
                }
            )
            continue

        raw_dest = staging_dir / f"press_{item_id:02d}_{_safe_slug(slug_base, max_len=42)}{ext}"
        ok, err = _download_file(session, img_url, raw_dest, max_bytes=25 * 1024 * 1024)
        if not ok or not raw_dest.is_file() or raw_dest.stat().st_size == 0:
            raw_dest.unlink(missing_ok=True)
            continue

        try:
            with Image.open(raw_dest) as im:
                im.load()
                orig_w, orig_h = ImageOps.exif_transpose(im).size
        except Exception:
            raw_dest.unlink(missing_ok=True)
            continue

        # Bộ lọc kích thước thực >= 600px (loại avatar, icon, banner quảng cáo, nút chia sẻ)
        if max(orig_w, orig_h) < eff_min_edge or min(orig_w, orig_h) < 120:
            raw_dest.unlink(missing_ok=True)
            print(f"   ⏭️ Loại ảnh nhỏ/banner ({orig_w}x{orig_h} < {eff_min_edge}px): {Path(img_url).name}")
            continue

        norm_path, (w, h), norm_err = _normalize_candidate_jpeg(raw_dest, max_long_edge=CANDIDATE_MAX_LONG_EDGE)
        if norm_path is None:
            print(f"   ⚠️ Lỗi chuẩn hóa ảnh báo chí: {norm_err}")
            continue

        sha256 = _sha256_file(norm_path)
        if sha256 in seen_sha256:
            norm_path.unlink(missing_ok=True)
            continue
        seen_sha256.add(sha256)

        final_name = f"press_{item_id:02d}_{_safe_slug(slug_base, max_len=42)}.jpg"
        final_path = staging_dir / final_name
        if norm_path.resolve() != final_path.resolve():
            os.replace(norm_path, final_path)

        entry = {
            "id": item_id,
            "filename": final_path.name,
            "width": w,
            "height": h,
            "caption": cand.get("caption") or "",
            "article_url": cand.get("article_url") or "",
            "publisher": cand.get("publisher") or "",
            "image_url": img_url,
            "license": EDITORIAL_PRESS_LICENSE,
            "sha256": sha256,
        }
        manifest_items.append(entry)
        print(f"✓ PRESS #{item_id}: {final_path.name} ({w}x{h} | {entry['publisher']} | {entry['caption'][:50]})")

    contact_sheet_path: Optional[Path] = None
    if not dry_run and manifest_items:
        contact_sheet_path = create_press_contact_sheet(staging_dir, manifest_items)

    manifest_payload = {
        "kind": "vn_press_staging",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "min_long_edge": eff_min_edge,
        "article_urls": list(article_urls),
        "contact_sheet": contact_sheet_path.name if contact_sheet_path else "",
        "count": len(manifest_items),
        "items": manifest_items,
    }
    manifest_path = staging_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📰 Đã lưu {len(manifest_items)} ảnh báo chí/bảo tàng tại: {staging_dir}")
    if contact_sheet_path:
        print(f"🖼️ Contact sheet duyệt nhanh: {contact_sheet_path}")
    return manifest_payload


def promote_staged_press_images(
    project_dir: Path,
    promote_specs: Sequence[str],
    *,
    args: Optional[argparse.Namespace] = None,
) -> List[Path]:
    """Đưa ảnh đã duyệt từ `<project_dir>/staging_press/` vào `assets/{scene:03d}.jpg` và ghi `archive_sources.json`."""
    ep_dir = project_dir.resolve()
    staging_dir = ep_dir / "staging_press"
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy manifest staging báo chí tại: {manifest_path}")

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest_data.get("items") or []
    by_id: Dict[int, Dict[str, Any]] = {int(it["id"]): dict(it) for it in items if "id" in it}

    pairs: List[Tuple[int, int]] = []
    for raw_spec in promote_specs:
        for tok in str(raw_spec or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", tok)
            if not m:
                raise ValueError(f"--promote phải có dạng <id>:<scene_num> (vd: 1:3), nhận: '{tok}'")
            pairs.append((int(m.group(1)), int(m.group(2))))

    if not pairs:
        raise ValueError("Không có cặp <id>:<scene_num> hợp lệ nào cho --promote.")

    eff_args = args or argparse.Namespace(
        assets_dir=None,
        youtube_16x9=False,
        target_size=None,
        crop_margins=None,
        note="",
    )
    assets_dir = _resolve_commit_assets_dir(ep_dir, eff_args)
    assets_dir.mkdir(parents=True, exist_ok=True)
    target_size = _parse_target_size(eff_args)
    is_parallel = assets_dir.name != "assets"

    committed_paths: List[Path] = []
    for press_id, scene_num in pairs:
        if press_id not in by_id:
            raise KeyError(f"Không tìm thấy ảnh id={press_id} trong {manifest_path}")
        meta = by_id[press_id]
        src_img = staging_dir / str(meta["filename"])
        if not src_img.is_file():
            raise FileNotFoundError(f"Không tìm thấy file ảnh staging: {src_img}")

        out_jpg = assets_dir / f"{scene_num:03d}.jpg"
        with Image.open(src_img) as img:
            rgb = ImageOps.exif_transpose(img).convert("RGB")
            if target_size is not None:
                rgb = ImageOps.fit(rgb, target_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.35))
            w, h = rgb.size
            rgb.save(out_jpg, format="JPEG", quality=95, subsampling=0)

        publisher = str(meta.get("publisher") or resolve_vn_press_publisher(meta.get("article_url") or "")).strip()
        caption = str(meta.get("caption") or meta.get("filename") or out_jpg.name).strip()
        entry = {
            "scene_id": scene_num,
            "asset_file": f"{assets_dir.name}/{out_jpg.name}",
            "media_type": "image",
            "title": caption,
            "provider": "vn_press",
            "publisher": publisher,
            "author": publisher,
            "year_or_date": "",
            "license": EDITORIAL_PRESS_LICENSE,
            "source_url": str(meta.get("article_url") or meta.get("image_url") or ""),
            "image_url": str(meta.get("image_url") or ""),
            "crop_margins": "",
            "resolution": f"{w}x{h}",
            "sha256": _sha256_file(out_jpg),
            "note": getattr(eff_args, "note", "") or f"Promoted from staging_press #{press_id}",
            "committed_at": datetime.now().isoformat(timespec="seconds"),
        }
        sources_path = _update_archive_sources(ep_dir, scene_num, entry, is_parallel_16x9=is_parallel)
        committed_paths.append(out_jpg)
        print(f"✓ Đã promote ảnh #{press_id} -> {out_jpg} ({w}x{h} | {EDITORIAL_PRESS_LICENSE}) | Nguồn: {sources_path}")

    return committed_paths


def run_mode_vn_press(args: argparse.Namespace) -> int:
    session = _make_session()
    urls = list(getattr(args, "urls", None) or [])
    queries = list(getattr(args, "query", None) or [])
    if not urls and queries:
        urls = _discover_vn_press_article_urls(session, queries, limit=max(4, min(10, args.limit)))
    if not urls:
        raise ValueError("Chế độ vn_press yêu cầu truyền --urls <url1> <url2>... hoặc --query '...'")

    staging_dir = _resolve_staging_press_dir(args)
    project_dir = staging_dir.parent
    min_edge = max(VN_PRESS_MIN_EDGE, int(getattr(args, "min_long_edge", VN_PRESS_MIN_EDGE) or VN_PRESS_MIN_EDGE))
    stage_vn_press_images(
        session,
        urls,
        project_dir=project_dir,
        min_long_edge=min_edge,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    return 0


def run_mode_promote(args: argparse.Namespace) -> int:
    if not getattr(args, "episode_dir", None):
        raise ValueError("--promote yêu cầu truyền --episode-dir <thư_mục_tập>.")
    specs = list(getattr(args, "promote", None) or [])
    promote_staged_press_images(Path(args.episode_dir), specs, args=args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Săn tư liệu lịch sử thật đa nguồn V5.5 (Wikimedia + Internet Archive Video/Image + NARA + Báo chí/Bảo tàng VN).",
    )
    p.add_argument(
        "--mode",
        required=False,
        default=None,
        choices=["images", "videos", "commit-image", "commit-video", "vn-press", "promote"],
        help="Chế độ chạy (tùy chọn khi đã dùng --urls, --promote hoặc --media-type).",
    )
    p.add_argument(
        "--media-type",
        choices=["image", "video", "all"],
        default=None,
        help="Loại tư liệu cần tìm: image / video / all (mặc định: image, hoặc theo --mode).",
    )
    p.add_argument("--query", action="append", help="Từ khóa tìm kiếm (có thể truyền nhiều --query cho Việt/Anh/Pháp).")
    p.add_argument("--urls", nargs="+", help="Danh sách URL bài báo / bảo tàng VN (qdnd.vn, baotanglichsu.vn...) để bóc ảnh vào staging_press/.")
    p.add_argument("--promote", action="append", help="Nạp ảnh từ staging_press/ vào assets/ theo cú pháp <id>:<scene_num> (vd: --promote 1:3).")
    p.add_argument("--episode-dir", "--project-dir", dest="episode_dir", help="Đường dẫn thư mục tập (vd: story_mode/projects/.../Dien_Bien_Phu_Tap_04).")
    p.add_argument("--episode-name", help="Tên thư mục con lưu ứng viên trong automation/debug/archive_candidates/.")
    p.add_argument("--out-dir", help="Thư mục tùy chỉnh lưu ứng viên images/videos (ghi đè --episode-name).")
    p.add_argument("--limit", type=int, default=8, help="Số lượng ứng viên tối đa cần tải (mặc định: 8).")
    p.add_argument("--min-long-edge", type=int, default=800, help="Cạnh dài tối thiểu cho ảnh quốc tế (mặc định: 800px; vn_press mặc định >= 600px).")
    p.add_argument("--min-short-edge", type=int, default=360, help="Cạnh ngắn tối thiểu cho video (mặc định: 360px).")
    p.add_argument("--max-size-mb", type=int, default=250, help="Dung lượng file video tối đa cho phép tải (mặc định: 250MB).")
    p.add_argument(
        "--sources",
        default=DEFAULT_SOURCES,
        help="Danh sách nguồn phân tách dấu phẩy: wikimedia,internet_archive,nara,vn_press hoặc all (mặc định: wikimedia,internet_archive,nara).",
    )
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
    p.add_argument("--scene", type=int, help="Số thứ tự cảnh đích (1 -> assets/001.jpg hoặc assets_16x9/001.jpg).")
    p.add_argument("--assets-dir", "--output-dir", dest="assets_dir", help="Thư mục đích khi commit (mặc định assets/ hoặc assets_16x9/ nếu bật --youtube-16x9).")
    p.add_argument("--youtube-16x9", action="store_true", help="Nạp tư liệu bản ngang 1920x1080 vào <episode_dir>/assets_16x9/ không đụng assets/ cũ.")
    p.add_argument("--target-size", help="Kích thước chuẩn hóa đầu ra dạng WxH (vd: 1920x1080).")
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
    if getattr(args, "promote", None):
        return run_mode_promote(args)
    if getattr(args, "urls", None) or args.mode == "vn-press":
        return run_mode_vn_press(args)
    if args.mode == "commit-image":
        if not args.input or not args.episode_dir or args.scene is None:
            raise ValueError("--mode commit-image yêu cầu --input, --episode-dir và --scene.")
        return run_mode_commit_image(args)
    if args.mode == "commit-video":
        if not args.input or not args.episode_dir or args.scene is None:
            raise ValueError("--mode commit-video yêu cầu --input, --episode-dir và --scene.")
        return run_mode_commit_video(args)
    if args.mode == "promote":
        return run_mode_promote(args)

    media_type = args.media_type
    if media_type is None:
        media_type = "video" if args.mode == "videos" else "image"

    if media_type == "video":
        return run_mode_videos(args)
    if media_type == "all":
        rc_img = run_mode_images(args)
        rc_vid = run_mode_videos(args)
        return rc_img or rc_vid
    return run_mode_images(args)


if __name__ == "__main__":
    raise SystemExit(main())
