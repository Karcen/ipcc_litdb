"""Small standard-library renderer for the verified article additions."""

import re

FIELD_ORDER = (
    "author", "title", "journal", "year", "volume", "number", "pages",
    "doi", "url", "note",
)


def format_pages(raw):
    """Use BibTeX range punctuation, including abbreviated numeric end pages."""
    if not raw:
        return ""
    pages = re.sub(r"^\s*pp?\.\s*", "", str(raw).strip(), flags=re.IGNORECASE)
    match = re.fullmatch(r"(\d+)\s*(?:-{1,3}|–|—)\s*(\d+)", pages)
    if match is None:
        return pages
    first, last = match.groups()
    if len(last) < len(first):
        last = first[:len(first) - len(last)] + last
    return f"{first}--{last}"


def render_entry(entry_type, key, fields):
    """Render populated fields in a fixed order with aligned field names."""
    names = [name for name in FIELD_ORDER if fields.get(name)]
    names.extend(name for name in fields if name not in names and fields[name])
    if not names:
        return f"@{entry_type}{{{key},\n}}"
    width = max(map(len, names))
    body = ",\n".join(
        f"  {name.ljust(width)} = {{{fields[name]}}}" for name in names
    )
    return f"@{entry_type}{{{key},\n{body}\n}}"
