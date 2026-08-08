"""Grade / language / product-line classification.

CGC 10 Pristine and CGC 10 Gem Mint are DIFFERENT grades with different
markets (observed 12x price gap on the same card). Never pool them, and
never guess when the tier is unstated.
"""

import re

# Tier constants
CGC10_PRISTINE = "CGC10_PRISTINE"
CGC10_GEM = "CGC10_GEM"
CGC10_UNSPEC = "CGC10_UNSPEC"
PSA10 = "PSA10"
OTHER = "OTHER"

_PRISTINE_RE = re.compile(r"\b(pristine|p10|10\s*pristine|pristine\s*10)\b", re.I)
_GEM_RE = re.compile(r"\b(gem\s*mint|gem)\b", re.I)
# Both word orders are common on eBay: "CGC 10 Gem Mint" AND "CGC Gem Mint 10".
# Requiring the number right after CGC threw away most real comps.
_CGC10_RE = re.compile(
    r"\bCGC\s*(?:gem\s*(?:mint|mt)|pristine|perfect)?\s*10(?!\.?\d)", re.I)
_PSA10_RE = re.compile(r"\bPSA\s*(?:gem\s*(?:mint|mt)\s*)?10(?!\.?\d)", re.I)


def classify_grade(title):
    t = title or ""
    if _CGC10_RE.search(t):
        if _PRISTINE_RE.search(t):
            return CGC10_PRISTINE
        if _GEM_RE.search(t):
            return CGC10_GEM
        return CGC10_UNSPEC
    if _PSA10_RE.search(t):
        return PSA10
    return OTHER


_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def extract_year(title):
    """First plausible year in the title. Unreliable for matching (Ancient Mew
    appears as both 1999 and 2000) but safe for a <= 2009 scope filter."""
    m = _YEAR_RE.search(title or "")
    return int(m.group(1)) if m else None


# Sets/keywords that imply a Japanese card even when the word "Japanese" is
# omitted from the title (a confirmed Fanatics habit).
_JP_HINTS = re.compile(
    r"(japanese|corocoro|rocket gang|carddass|vending series|neo premium file|"
    r"gym leaders?'? deck|hanada|masaki|gb promo|topsun|bandai)", re.I)


def detect_language(title):
    return "JA" if _JP_HINTS.search(title or "") else "EN"


def detect_product_line(title):
    """Bandai Carddass (and Topps) sit in the same taxonomy but are separate
    collectible markets with different buyer pools. Flag, don't pool."""
    t = (title or "").lower()
    if "carddass" in t or "bandai" in t:
        return "BANDAI"
    if "topps" in t:
        return "TOPPS"
    if "topsun" in t:
        return "TOPSUN"
    return "TCG"


def is_pokemon(title):
    t = (title or "").lower()
    return "pokemon" in t or "pokémon" in t
