"""Normalization rules used by the upstream collection pipeline."""
from __future__ import annotations

import re
import unicodedata

DOI_RE = re.compile(r"^10\.\d{4,9}/\S{4,}$")


def normalise_doi(d: str) -> str:
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip().rstrip(".,;)")


def is_valid_doi(d: str) -> bool:
    return bool(d) and bool(DOI_RE.match(d)) and d.count("(") == d.count(")")


def normalise_title(s) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()
