"""Quote verification: an extracted claim is only kept if its quote really appears in the cited source."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_MARKDOWN = re.compile(r"[`*_>#~|]+")
_WS = re.compile(r"\s+")
_MIN_QUOTE_CHARS = 12


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = _MARKDOWN.sub(" ", text)
    return _WS.sub(" ", text).strip().lower()


def verify_quote(quote: str, source_text: str, *, min_ratio: float = 0.9) -> bool:
    """True if `quote` occurs in `source_text`, tolerating markdown, whitespace and minor tokenisation drift.

    Models sometimes drop a markdown marker or re-flow a line; an exact substring check would reject those,
    while a loose similarity check would accept paraphrases. We require a contiguous match covering
    at least `min_ratio` of the quote.
    """
    q = normalize(quote).strip(" .\"'…")
    if len(q) < _MIN_QUOTE_CHARS:
        return False
    s = normalize(source_text)
    if q in s:
        return True
    match = SequenceMatcher(None, s, q, autojunk=False).find_longest_match(0, len(s), 0, len(q))
    return match.size >= min_ratio * len(q)
