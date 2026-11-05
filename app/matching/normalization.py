import hashlib
import json
import re
import unicodedata
from datetime import date


def strict_normalize(name: str) -> str:
    """Normalize artist/venue name for exact matching (NFKC + casefold)."""
    text = unicodedata.normalize("NFKC", name)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u00a0", " ")
    text = " ".join(text.casefold().split())
    return text.strip()


def loose_normalize(name: str) -> str:
    """Helper normalization: diacritics + insignificant punctuation."""
    text = unicodedata.normalize("NFKC", name)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = " ".join(text.casefold().split())
    return text.strip()


def normalize_city(city: str | None) -> str | None:
    if not city:
        return None
    return loose_normalize(city)


def normalize_venue(venue: str | None) -> str | None:
    if not venue:
        return None
    return strict_normalize(venue)


def content_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def is_past_event(local_date: date | None) -> bool:
    if local_date is None:
        return False
    return local_date < date.today()
