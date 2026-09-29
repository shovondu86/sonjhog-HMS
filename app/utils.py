import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_phone(raw: Optional[str]) -> str:
    """11-digit national form: keep digits only, drop a leading country code 88."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("88") and len(digits) > 11:
        digits = digits[2:]
    return digits


_SPLIT = re.compile(r"[\s,;:/&()\[\]\-\u2013\u2014.]+")


def split_words(text: str) -> list:
    # Deliberately NOT \w+: Bangla vowel signs are combining marks and would split words.
    return [w for w in _SPLIT.split(text.lower()) if w]


def word_prefix_match(query: str, texts: Iterable[Optional[str]]) -> bool:
    """Every query token must be a prefix of some word in the given texts."""
    tokens = split_words(query)
    if not tokens:
        return True
    words = [w for t in texts if t for w in split_words(t)]
    return all(any(w.startswith(tok) for w in words) for tok in tokens)


def like_contains(column, term: str):
    """Case-insensitive substring match; % and _ in `term` are matched literally."""
    return func.lower(column).contains(term.lower(), autoescape=True)


def apply_updates(obj, data: dict, non_nullable: Iterable[str] = ()) -> None:
    """Partial update: an explicit null on a non-nullable field means 'unchanged'."""
    non_nullable = set(non_nullable)
    for key, value in data.items():
        if value is None and key in non_nullable:
            continue
        setattr(obj, key, value)
