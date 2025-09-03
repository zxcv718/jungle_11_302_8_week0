import bleach
import markdown2

ALLOWED_TAGS = [
    'p','br','strong','em','ul','ol','li','blockquote','code','pre','h1','h2','h3','h4','h5','h6','hr',
    'table','thead','tbody','tr','th','td','span','del','ins','sup','sub','a','img'
]
ALLOWED_ATTRS = {'a': ['href','title','rel','target'], 'img': ['src','alt','title'], 'span': ['class']}
ALLOWED_PROTOCOLS = ['http','https','mailto']


def render_markdown_sanitized(md_src: str) -> str | None:
    if not md_src:
        return None
    try:
        html_raw = markdown2.markdown(md_src)
        html_sanitized = bleach.clean(html_raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True)
        return html_sanitized
    except Exception:
        return None
