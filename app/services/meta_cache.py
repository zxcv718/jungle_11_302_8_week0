from __future__ import annotations

# Minimal fallback meta cache service to satisfy imports.
# This avoids breaking the app if the cache module was removed.
# It simply fetches metadata directly without caching.

from typing import Dict, Any

from metadata import fetch_and_extract_metadata, normalize_url


def get_or_fetch(url: str) -> Dict[str, Any]:
    """Fetch URL metadata without persistence.

    Returns a plain dict compatible with templates and callers.
    """
    try:
        norm, err = normalize_url(url)
        if err:
            return {}
        meta = fetch_and_extract_metadata(norm)
        return meta.to_dict() if hasattr(meta, "to_dict") else {}
    except Exception:
        return {}
