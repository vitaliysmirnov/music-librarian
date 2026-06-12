import re

DEFAULT_MASK = "{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released})"

KNOWN_TOKENS = {"artist", "title", "year_recorded", "year_released", "catalog_number", "media"}

# Bare (unbracketed) tokens that are always required in the pattern
_REQUIRED_TOKENS = {"artist", "year_recorded", "title"}

# These tokens may contain spaces when used bare (without brackets)
_GREEDY_TOKENS = {"artist", "title"}
_YEAR_TOKENS = {"year_recorded", "year_released"}


def _token_pattern(token: str, bracket: str | None) -> str:
    """Return the regex fragment for a token value (not including group wrapper)."""
    if bracket == "square":
        return r"[^\]]+"   # anything except closing bracket
    if bracket == "round":
        return r"[^)]+"    # anything except closing paren
    # Bare token rules:
    # - year fields → exactly 4 digits
    # - artist/title → greedy multi-word
    # - everything else → single word (no spaces)
    if token in _YEAR_TOKENS:
        return r"\d{4}"
    if token in _GREEDY_TOKENS:
        return r".+?"
    return r"\S+"


def _tokenize(mask: str) -> list[tuple[str, str, str | None]]:
    """Split mask into (kind, value, bracket) tuples.

    kind is 'literal' or 'token'.
    bracket is None (bare), 'square' ([]), or 'round' (()).
    """
    parts: list[tuple[str, str, str | None]] = []
    i = 0
    lit_buf = ""

    while i < len(mask):
        # [{token}]
        m = re.match(r"\[\{(\w+)\}\]", mask[i:])
        if m:
            if lit_buf:
                parts.append(("literal", lit_buf, None))
                lit_buf = ""
            parts.append(("token", m.group(1), "square"))
            i += m.end()
            continue

        # ({token})
        m = re.match(r"\(\{(\w+)\}\)", mask[i:])
        if m:
            if lit_buf:
                parts.append(("literal", lit_buf, None))
                lit_buf = ""
            parts.append(("token", m.group(1), "round"))
            i += m.end()
            continue

        # {token}
        m = re.match(r"\{(\w+)\}", mask[i:])
        if m:
            if lit_buf:
                parts.append(("literal", lit_buf, None))
                lit_buf = ""
            parts.append(("token", m.group(1), None))
            i += m.end()
            continue

        lit_buf += mask[i]
        i += 1

    if lit_buf:
        parts.append(("literal", lit_buf, None))

    return parts


def _lit(text: str) -> str:
    """Convert a literal mask segment to a regex fragment.

    Spaces become \\s* (flexible); other characters are individually escaped.
    Consecutive spaces collapse into a single \\s*.
    """
    parts: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == " ":
            parts.append(r"\s*")
            while i < len(text) and text[i] == " ":
                i += 1
        else:
            parts.append(re.escape(text[i]))
            i += 1
    return "".join(parts)


def mask_to_regex(mask: str) -> re.Pattern:
    """Compile a mask string into a named-group regex.

    Required bare tokens (artist, year_recorded, title) keep their surrounding
    literals mandatory.  All other bare tokens become optional groups that absorb
    the preceding whitespace literal so folders lacking those fields still match.
    """
    parts = _tokenize(mask)
    result = r"^"
    i = 0

    while i < len(parts):
        kind, value, bracket = parts[i]

        if kind == "literal":
            # If the next part is a non-required bare token, skip this literal —
            # the optional group for that token will start with \s* instead.
            next_p = parts[i + 1] if i + 1 < len(parts) else None
            if (next_p and next_p[0] == "token" and
                    next_p[2] is None and next_p[1] not in _REQUIRED_TOKENS):
                i += 1
                continue
            result += _lit(value)

        else:  # token
            pat = _token_pattern(value, bracket)
            if bracket == "square":
                result += r"(?:\s*\[(?P<" + value + r">" + pat + r")\])?"
            elif bracket == "round":
                result += r"(?:\s*\((?P<" + value + r">" + pat + r")\))?"
            elif value in _REQUIRED_TOKENS:
                result += r"(?P<" + value + r">" + pat + r")"
            else:
                # Optional bare token — preceding whitespace absorbed into group
                result += r"(?:\s*(?P<" + value + r">" + pat + r"))?"

        i += 1

    result += r"\s*$"
    return re.compile(result)


def validate_mask(mask: str) -> str | None:
    """Return an error string, or None if the mask is valid."""
    required = {"artist", "year_recorded", "title"}
    found = set(re.findall(r"\{(\w+)\}", mask))
    missing = required - found
    if missing:
        return "Missing required tokens: " + ", ".join(f"{{{t}}}" for t in sorted(missing))
    try:
        mask_to_regex(mask)
    except re.error as e:
        return f"Invalid pattern: {e}"
    return None


def parse_with_mask(name: str, pattern: re.Pattern) -> dict | None:
    """Return a dict of named groups, or None if the name doesn't match."""
    m = pattern.match(name.strip())
    if not m:
        return None
    return {k: v for k, v in m.groupdict().items() if v is not None}


def get_custom_tokens(mask: str) -> list[str]:
    """Return tokens not in KNOWN_TOKENS, in order of appearance (no duplicates)."""
    seen: set[str] = set()
    result: list[str] = []
    for token in re.findall(r"\{(\w+)\}", mask):
        if token not in KNOWN_TOKENS and token not in seen:
            seen.add(token)
            result.append(token)
    return result
