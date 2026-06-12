import re
from dataclasses import dataclass, field

from src.scanner.mask import mask_to_regex, DEFAULT_MASK, parse_with_mask, KNOWN_TOKENS

_DEFAULT_PATTERN: re.Pattern = mask_to_regex(DEFAULT_MASK)


@dataclass
class ParsedRelease:
    artist: str
    year_recorded: str
    title: str
    catalog_number: str | None
    media: str | None
    year_released: str | None
    extras: dict = field(default_factory=dict)


def parse_folder_name(name: str, pattern: re.Pattern | None = None) -> ParsedRelease | None:
    groups = parse_with_mask(name, pattern or _DEFAULT_PATTERN)
    if not groups:
        return None
    artist = groups.get("artist", "").strip()
    year_recorded = groups.get("year_recorded", "")
    title = groups.get("title", "").strip()
    if not (artist and year_recorded and title):
        return None
    extras = {k: v for k, v in groups.items() if k not in KNOWN_TOKENS}
    return ParsedRelease(
        artist=artist,
        year_recorded=year_recorded,
        title=title,
        catalog_number=groups.get("catalog_number"),
        media=groups.get("media"),
        year_released=groups.get("year_released"),
        extras=extras,
    )
