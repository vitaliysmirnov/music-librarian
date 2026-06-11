import re
from dataclasses import dataclass

# Pattern: Artist - YearRecorded - Title [Catalog] [Media] (YearReleased)
# Brackets and parens are optional. Multiple bracket groups handled greedily.
_PATTERN = re.compile(
    r"^(?P<artist>.+?)"
    r"\s*-\s*"
    r"(?P<year_recorded>\d{4})"
    r"\s*-\s*"
    r"(?P<title>.+?)"
    r"(?:\s*\[(?P<catalog>[^\]]+)\])?"
    r"(?:\s*\[(?P<media>[^\]]+)\])?"
    r"(?:\s*\((?P<year_released>\d{4})\))?"
    r"\s*$"
)


@dataclass
class ParsedRelease:
    artist: str
    year_recorded: str
    title: str
    catalog_number: str | None
    media: str | None
    year_released: str | None


def parse_folder_name(name: str) -> ParsedRelease | None:
    m = _PATTERN.match(name.strip())
    if not m:
        return None
    return ParsedRelease(
        artist=m.group("artist").strip(),
        year_recorded=m.group("year_recorded"),
        title=m.group("title").strip(),
        catalog_number=m.group("catalog"),
        media=m.group("media"),
        year_released=m.group("year_released"),
    )
