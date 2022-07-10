"""Extract 11-character YouTube watch IDs from common URL formats."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, urlsplit

WATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Input:
    watch_id: str
    source_url: str


def is_valid_watch_id(text: str) -> bool:
    return isinstance(text, str) and WATCH_ID_RE.fullmatch(text.strip()) is not None


def extract_watch_id(input_text: str | None) -> str:
    if not isinstance(input_text, str) or not input_text.strip():
        raise InputError("Expected a non-empty source")
    text = input_text.strip()
    if is_valid_watch_id(text):
        return text
    url = urlsplit(text if "://" in text else "https://" + text)
    host = (url.hostname or "").lower()
    parts = url.path.strip("/").split("/")
    candidate = ""
    if host in ("youtu.be", "www.youtu.be"):
        candidate = parts[0]
    elif host == "youtube.com" or host.endswith(".youtube.com") or host == "www.youtube-nocookie.com":
        if parts[0] == "watch":
            candidate = parse_qs(url.query).get("v", [""])[0]
        elif parts[0] in ("shorts", "embed") and len(parts) == 2:
            candidate = parts[1]
    if not is_valid_watch_id(candidate):
        raise InputError("No YouTube video id in source")
    return candidate


def normalize_inputs(inputs, dedupe: bool = True) -> list[str]:
    sources = [inputs] if isinstance(inputs, str) else list(inputs or [])
    output = []
    seen = set()
    for source in sources:
        if source is None or (isinstance(source, str) and not source.strip()):
            continue
        wid = extract_watch_id(source)
        if not dedupe or wid not in seen:
            output.append(wid)
            seen.add(wid)
    return output
