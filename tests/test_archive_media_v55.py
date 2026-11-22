"""Unit tests (100% offline, mock HTTP) cho V5.5 Multi-Source Archive Media Fetcher & Series Compiler Audit."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image

from automation.scripts import fetch_archive_media as fam
from core.series_compiler import (
    ChapterMarker,
    audit_series_licenses,
    build_youtube_description,
    format_editorial_press_summary_line,
    render_end_credits_image,
)


def _make_jpeg_bytes(width: int, height: int, color: tuple[int, int, int] = (90, 110, 130)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        json_data: Any = None,
        content: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.content = content or (text.encode("utf-8") if text else b"")
        self.headers = headers or {"Content-Length": str(len(self.content))}
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    def iter_content(self, chunk_size: int = 65536):
        data = self.content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _FakeSession:
    def __init__(self, routes: Dict[str, _FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        for pattern, resp in self.routes.items():
            if pattern in url:
                return resp
        return _FakeResponse(status_code=404, text="Not found")


# ==============================================================================
# 1. Kiểm tra parse_sources_arg, NARA API & Internet Archive (Images + Videos)
# ==============================================================================
def test_parse_sources_and_international_archives_mock():
    assert fam.parse_sources_arg(None) == ["wikimedia", "internet_archive", "nara"]
    assert fam.parse_sources_arg("wikimedia,archive,nara,vn_press") == [
        "wikimedia",
        "internet_archive",
        "nara",
        "vn_press",
    ]
    assert fam.parse_sources_arg("all") == ["wikimedia", "internet_archive", "nara", "vn_press"]

    # Mock NARA API response + IA Search/Metadata response
    nara_payload = {
        "opaResponse": {
            "results": {
                "result": [
                    {
                        "naId": "19540507",
                        "description": {
                            "item": {
                                "title": "Aerial Reconnaissance of Dien Bien Phu Valley, 1954",
                                "scopeAndContentNote": "U.S. military attaché archival photograph in Indochina.",
                                "productionDateArray": [
                                    {"proposableQualifiableDate": {"logicalDate": "1954-04-18"}}
                                ],
                            }
                        },
                        "objects": {
                            "object": [
                                {
                                    "file": {
                                        "@url": "https://catalog.archives.gov/OpaAPI/media/19540507/content/dbp_1954.jpg"
                                    }
                                }
                            ]
                        },
                    }
                ]
            }
        }
    }
    ia_img_search = {
        "response": {
            "docs": [
                {
                    "identifier": "indochina_1954_photos",
                    "title": "Indochina 1954 Archival Press Photos",
                    "year": "1954",
                    "creator": "USIS Archive",
                    "rights": "Public Domain",
                }
            ]
        }
    }
    ia_img_meta = {
        "files": [
            {"name": "indochina_1954_thumb.jpg", "size": "12000", "width": "180", "height": "120"},
            {
                "name": "indochina_1954_main.jpg",
                "source": "original",
                "size": "480000",
                "width": "1400",
                "height": "1000",
            },
        ]
    }
    ia_vid_search = {
        "response": {
            "docs": [
                {
                    "identifier": "universal_newsreel_1954_dbp",
                    "title": "Universal Newsreel 1954: French Supply Drop at Dien Bien Phu",
                    "year": "1954",
                    "creator": "Universal Newsreel",
                    "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
                }
            ]
        }
    }
    ia_vid_meta = {
        "files": [
            {
                "name": "universal_newsreel_1954_dbp.mp4",
                "format": "h.264",
                "source": "original",
                "size": str(15 * 1024 * 1024),
                "width": "1280",
                "height": "720",
            }
        ]
    }

    session_nara = _FakeSession({fam.NARA_API_URL: _FakeResponse(json_data=nara_payload)})
    nara_items = fam._search_nara_images(session_nara, ["Dien Bien Phu 1954"], limit=4)
    assert len(nara_items) == 1
    assert nara_items[0]["provider"] == "nara"
    assert nara_items[0]["license"] == "Public Domain — U.S. National Archives"
    assert nara_items[0]["url"].endswith("dbp_1954.jpg")
    assert nara_items[0]["page_url"] == "https://catalog.archives.gov/id/19540507"

    session_ia_img = _FakeSession(
        {
            fam.IA_SEARCH_URL: _FakeResponse(json_data=ia_img_search),
            "https://archive.org/metadata/indochina_1954_photos": _FakeResponse(json_data=ia_img_meta),
        }
    )
    ia_imgs = fam._search_internet_archive_images(session_ia_img, ["Indochina 1954"], limit=4)
    assert len(ia_imgs) == 1
    assert ia_imgs[0]["provider"] == "internet_archive"
    assert ia_imgs[0]["url"].endswith("indochina_1954_main.jpg")
    assert ia_imgs[0]["width"] == 1400

    session_ia_vid = _FakeSession(
        {
            fam.IA_SEARCH_URL: _FakeResponse(json_data=ia_vid_search),
            "https://archive.org/metadata/universal_newsreel_1954_dbp": _FakeResponse(json_data=ia_vid_meta),
        }
    )
    ia_vids = fam._search_internet_archive_videos(
        session_ia_vid, ["Dien Bien Phu newsreel"], limit=4, max_bytes=100 * 1024 * 1024
    )
    assert len(ia_vids) == 1
    assert ia_vids[0]["provider"] == "internet_archive"
    assert ia_vids[0]["ext"] == ".mp4"
    assert "universal_newsreel_1954_dbp.mp4" in ia_vids[0]["url"]


# ==============================================================================
# 2. Parse HTML kiểu qdnd.vn / baotanglichsu.vn + Lọc ảnh nhỏ < 600px + Staging + Promote
# ==============================================================================
def test_vn_press_html_extraction_dimension_filter_staging_and_promote(tmp_path):
    article_url = "https://www.qdnd.vn/quoc-phong-an-ninh/su-tich-bep-hoang-cam-721000"
    sample_html = """
    <html>
      <head><title>"Sự tích" bếp Hoàng Cầm</title></head>
      <body>
        <header><img src="/assets/images/logo-qdnd.png" alt="Logo" width="180" height="60"></header>
        <article class="detail-content">
          <table class="picBox" align="center">
            <tbody>
              <tr>
                <td>
                  <img src="/data/images/2023/03/15/hoang_cam_1952.jpg" alt="Anh nuôi Hoàng Cầm">
                </td>
              </tr>
              <tr>
                <td class="caption">Chiến sĩ thi đua Hoàng Cầm báo công tại Đại hội năm 1952. Ảnh: Tư liệu QĐND</td>
              </tr>
            </tbody>
          </table>

          <figure class="article-figure">
            <img data-src="https://file3.qdnd.vn/data/images/2023/03/15/bep_hoang_cam_dien_tap.jpg" alt="Diễn tập">
            <figcaption>Sử dụng bếp Hoàng Cầm trong diễn tập tại Sư đoàn 3, Quân khu 1.</figcaption>
          </figure>

          <!-- Ảnh nhỏ dưới 600px (avatar/icon trong bài) phải bị loại khi kiểm tra kích thước thực -->
          <div class="photo-box">
            <img src="/data/images/2023/03/15/small_thumbnail.jpg" alt="Ảnh nhỏ 400x300">
            <p class="caption">Ảnh thumbnail kích thước nhỏ dưới ngưỡng 600px.</p>
          </div>

          <!-- Ảnh nút share/icon bị loại ngay từ HTML -->
          <img src="/themes/default/images/icon-share-fb.svg" alt="Share">
        </article>
      </body>
    </html>
    """

    extracted = fam.extract_vn_press_images_from_html(sample_html, article_url)
    assert len(extracted) == 3
    assert extracted[0]["image_url"] == "https://www.qdnd.vn/data/images/2023/03/15/hoang_cam_1952.jpg"
    assert "Chiến sĩ thi đua Hoàng Cầm báo công" in extracted[0]["caption"]
    assert extracted[0]["publisher"] == "Báo Quân đội nhân dân (qdnd.vn)"

    assert extracted[1]["image_url"] == "https://file3.qdnd.vn/data/images/2023/03/15/bep_hoang_cam_dien_tap.jpg"
    assert "Sử dụng bếp Hoàng Cầm trong diễn tập" in extracted[1]["caption"]

    # Mock tải HTML + 3 file ảnh (2 ảnh >= 600px và 1 ảnh 400x300 < 600px)
    img1_bytes = _make_jpeg_bytes(1200, 820, (70, 75, 80))
    img2_bytes = _make_jpeg_bytes(960, 640, (50, 95, 65))
    small_bytes = _make_jpeg_bytes(400, 300, (180, 50, 50))

    session = _FakeSession(
        {
            article_url: _FakeResponse(text=sample_html),
            "https://www.qdnd.vn/data/images/2023/03/15/hoang_cam_1952.jpg": _FakeResponse(content=img1_bytes),
            "https://file3.qdnd.vn/data/images/2023/03/15/bep_hoang_cam_dien_tap.jpg": _FakeResponse(
                content=img2_bytes
            ),
            "https://www.qdnd.vn/data/images/2023/03/15/small_thumbnail.jpg": _FakeResponse(content=small_bytes),
        }
    )

    project_dir = tmp_path / "Bep_Hoang_Cam_Test"
    manifest = fam.stage_vn_press_images(
        session,
        [article_url],
        project_dir=project_dir,
        min_long_edge=600,
        limit=10,
    )

    # Ảnh 400x300 phải bị loại sạch, chỉ còn đúng 2 ảnh >= 600px
    assert manifest["count"] == 2
    assert len(manifest["items"]) == 2
    staging_dir = project_dir / "staging_press"
    assert (staging_dir / "manifest.json").is_file()
    assert (staging_dir / "contact_sheet.jpg").is_file()

    with Image.open(staging_dir / "contact_sheet.jpg") as cs_img:
        assert cs_img.mode == "RGB"
        assert cs_img.size[0] >= 400 and cs_img.size[1] >= 250
        assert np.asarray(cs_img).std() > 5.0

    item1, item2 = manifest["items"]
    assert item1["id"] == 1
    assert ( item1["width"], item1["height"]) == (1200, 820)
    assert item1["publisher"] == "Báo Quân đội nhân dân (qdnd.vn)"
    assert item1["license"] == "Editorial / Press Archive (Credit Required)"

    # Kiểm tra --promote 1:4 và 2:18 nạp thẳng vào assets/004.jpg, assets/018.jpg và cập nhật archive_sources.json
    promote_args = argparse.Namespace(
        assets_dir=None,
        youtube_16x9=False,
        target_size="1080x1920",
        crop_margins=None,
        note="Tư liệu Báo QĐND đã duyệt",
    )
    promoted = fam.promote_staged_press_images(project_dir, ["1:4", "2:18"], args=promote_args)
    assert len(promoted) == 2
    assert (project_dir / "assets" / "004.jpg").is_file()
    assert (project_dir / "assets" / "018.jpg").is_file()

    with Image.open(project_dir / "assets" / "004.jpg") as p_img:
        assert p_img.size == (1080, 1920)

    sources_json = json.loads((project_dir / "archive_sources.json").read_text(encoding="utf-8"))
    rec4 = sources_json["assets"]["004"]
    assert rec4["license"] == "Editorial / Press Archive (Credit Required)"
    assert rec4["provider"] == "vn_press"
    assert rec4["publisher"] == "Báo Quân đội nhân dân (qdnd.vn)"
    assert rec4["source_url"] == article_url
    assert "Chiến sĩ thi đua Hoàng Cầm" in rec4["title"]


# ==============================================================================
# 3. Kiểm toán nguồn editorial_credit_required trong core/series_compiler.py
# ==============================================================================
def test_series_compiler_audit_editorial_press_license_and_credits(tmp_path):
    ep_dir = tmp_path / "Tap_Bep_Hoang_Cam"
    ep_dir.mkdir()

    (ep_dir / "archive_sources.json").write_text(
        json.dumps(
            {
                "assets": {
                    "001": {
                        "scene_id": 1,
                        "title": "File:Dien_Bien_1954.jpg",
                        "author": "TTXVN",
                        "license": "Public domain",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Dien_Bien_1954.jpg",
                        "sha256": "pd_sha_001",
                    },
                    "004": {
                        "scene_id": 4,
                        "title": "Chiến sĩ thi đua Hoàng Cầm báo công năm 1952",
                        "provider": "vn_press",
                        "publisher": "Báo Quân đội nhân dân (qdnd.vn)",
                        "author": "Báo Quân đội nhân dân (qdnd.vn)",
                        "license": "Editorial / Press Archive (Credit Required)",
                        "source_url": "https://www.qdnd.vn/quoc-phong-an-ninh/su-tich-bep-hoang-cam-721000",
                        "sha256": "press_sha_004",
                    },
                    "006": {
                        "scene_id": 6,
                        "title": "Hiện vật Bếp Hoàng Cầm tại Bảo tàng",
                        "provider": "vn_press",
                        "publisher": "Bảo tàng Lịch sử Quốc gia (baotanglichsu.vn)",
                        "author": "Bảo tàng Lịch sử Quốc gia (baotanglichsu.vn)",
                        "license": "Editorial / Press Archive (Credit Required)",
                        "source_url": "https://baotanglichsu.vn/vi/Articles/3096/12345/bep-hoang-cam.html",
                        "sha256": "press_sha_006",
                    },
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = audit_series_licenses([ep_dir])
    assert report.has_risky_warnings is False
    assert len(report.risky_warnings) == 0
    assert len(report.public_domain) == 1
    assert len(report.editorial_credit_required) == 2
    assert report.to_dict()["counts"]["editorial_credit_required"] == 2

    summary_line = format_editorial_press_summary_line(report.editorial_credit_required)
    assert "Ảnh tư liệu tổng hợp từ Báo Quân đội nhân dân (qdnd.vn)" in summary_line
    assert "Bảo tàng Lịch sử Quốc gia (baotanglichsu.vn)" in summary_line

    chapters = [
        ChapterMarker(
            chapter_index=1,
            episode_id="Tap_Bep_Hoang_Cam",
            chapter_title="Bếp Hoàng Cầm",
            start_sec=0.0,
            episode_start_sec=2.5,
            duration_sec=95.0,
            card_duration_sec=2.5,
        )
    ]
    yt_desc = build_youtube_description("Bep_Hoang_Cam_Series", chapters, report, channel_name="Sử Việt")
    assert "NGUỒN TƯ LIỆU BÁO CHÍ & BẢO TÀNG (EDITORIAL / PRESS ARCHIVE):" in yt_desc
    assert "Ảnh tư liệu tổng hợp từ Báo Quân đội nhân dân (qdnd.vn)" in yt_desc
    assert "https://www.qdnd.vn/quoc-phong-an-ninh/su-tich-bep-hoang-cam-721000" in yt_desc

    credits_img = tmp_path / "end_credits_press.png"
    render_end_credits_image(report, credits_img, series_title="Bếp Hoàng Cầm", channel_name="Sử Việt")
    assert credits_img.is_file()
    with Image.open(credits_img) as im:
        assert im.size == (1920, 1080)
        assert np.asarray(im).std() > 5.0
