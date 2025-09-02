from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse


TIMEOUT = (5, 10)  # connect, read
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class Meta:
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    site_name: Optional[str] = None
    author: Optional[str] = None
    published_time: Optional[str] = None
    favicon: Optional[str] = None
    content_type: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def normalize_url(raw: str) -> Tuple[Optional[str], Optional[str]]:
    raw = raw.strip()
    if not raw:
        return None, "빈 URL 입니다."
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    try:
        p = urlparse(raw)
        if not p.scheme or not p.netloc:
            return None, "유효한 URL 형식이 아닙니다."
        return raw, None
    except Exception:
        return None, "유효한 URL 형식이 아닙니다."


def fetch_and_extract_metadata(url: str) -> Meta:
    meta = Meta(url=url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        # 네트워크 오류시 최소 필드만 반환
        return meta

    meta.content_type = r.headers.get("Content-Type")

    # HTML 파싱
    html = r.text or ""
    soup = BeautifulSoup(html, "html.parser")

    # Open Graph
    def og(name: str) -> Optional[str]:
        tag = soup.find("meta", property=f"og:{name}")
        return tag.get("content") if tag and tag.get("content") else None

    # Twitter Card
    def tw(name: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"name": f"twitter:{name}"})
        return tag.get("content") if tag and tag.get("content") else None

    # Generic meta
    def meta_name(name: str) -> Optional[str]:
        tag = soup.find("meta", attrs={"name": name})
        return tag.get("content") if tag and tag.get("content") else None

    # Title
    meta.title = og("title") or tw("title") or (soup.title.string.strip() if soup.title and soup.title.string else None)

    # Description
    meta.description = og("description") or tw("description") or meta_name("description")

    # Image (absolute URL로 정규화)
    img = og("image") or tw("image")
    if not img:
        # 첫 번째 <img> 사용 (과도한 사이즈 방지 불가)
        first_img = soup.find("img")
        if first_img and first_img.get("src"):
            img = first_img.get("src")
    if img:
        meta.image = urljoin(r.url, img)

    # Site name
    meta.site_name = og("site_name") or meta_name("application-name")

    # Author
    meta.author = meta_name("author") or tw("creator")

    # Published time
    meta.published_time = og("published_time") or og("pubdate") or meta_name("article:published_time")

    # Favicon
    icon = (
        soup.find("link", rel=lambda v: v and "icon" in ",".join(v).lower())
        or soup.find("link", attrs={"rel": "shortcut icon"})
    )
    if icon and icon.get("href"):
        meta.favicon = urljoin(r.url, icon.get("href"))
    else:
        # 기본 /favicon.ico 추정
        parsed = urlparse(r.url)
        meta.favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

    return meta
