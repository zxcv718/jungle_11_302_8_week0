import re

_md_img_re = re.compile(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)')
_html_img_re = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)


def first_image_from_markdown(contents: str) -> str | None:
    if not contents:
        return None
    m = _md_img_re.search(contents)
    if m:
        return m.group(1)
    m2 = _html_img_re.search(contents)
    if m2:
        return m2.group(1)
    return None


def to_plain_preview(contents: str) -> str:
    if not contents:
        return ""
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', contents)  # images
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)  # links -> text
    text = re.sub(r'\b(?:https?://|www\.)[^\s)]+', '', text)  # raw urls
    text = re.sub(r'`{1,3}[^`]*`{1,3}', lambda m: m.group(0).replace('`', ''), text)  # code ticks
    text = re.sub(r'[\*_~]{1,3}([^\*_~]+)[\*_~]{1,3}', r'\1', text)  # bold/italic/strike
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # headings
    text = re.sub(r'^\s{0,3}>\s?', '', text, flags=re.MULTILINE)  # blockquote
    text = re.sub(r'^\s*(?:[-*+]|\d+\.)\s+', '', text, flags=re.MULTILINE)  # lists
    text = re.sub(r'<[^>]+>', '', text)  # html tags
    text = re.sub(r'\s+', ' ', text).strip()
    return text
