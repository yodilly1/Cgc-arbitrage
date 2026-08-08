"""Card-title normalization: FC titles -> eBay search queries + dedupe keys.

Fanatics' Japanese naming is chaos (verified in their own catalog):
  - Japanese Team Rocket is listed as "Rocket Gang"
  - "Nintedo Error" is misspelled in Fanatics' own titles
  - CoroCoro / Corocoro case varies; word order is unstable
  - Card numbers inconsistently zero-padded (#6 vs #006)
A naive keyword join silently misses these cards entirely.
"""

import re

_GRADE_SUFFIX_RE = re.compile(
    r"\b(CGC|PSA|BGS|SGC)\s*(pristine\s*)?10(\.5)?\b.*$", re.I)
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"#0*(\d+)")

# FC-ism -> the term the eBay market actually uses
_EBAY_SYNONYMS = [
    (re.compile(r"\brocket gang\b", re.I), "Team Rocket"),
    (re.compile(r"\bnintedo\b", re.I), "Nintendo"),
    (re.compile(r"\bcorocoro\b", re.I), "CoroCoro"),
]


def strip_grade(title):
    return _WS_RE.sub(" ", _GRADE_SUFFIX_RE.sub("", title or "")).strip(" -–")


def ebay_query(title):
    """Primary eBay search string for an FC lot title."""
    q = strip_grade(title)
    for pat, repl in _EBAY_SYNONYMS:
        q = pat.sub(repl, q)
    q = _NUM_RE.sub(lambda m: "#" + m.group(1), q)   # #006 -> #6
    return _WS_RE.sub(" ", q).strip()


def query_variants(title):
    """Ordered fallback queries, broadest last. Try in order until comps appear."""
    primary = ebay_query(title)
    variants = [primary]
    no_num = _WS_RE.sub(" ", re.sub(r"#\d+", "", primary)).strip()
    if no_num != primary:
        variants.append(no_num)
    # drop the leading year — FC year attribution is unreliable
    no_year = re.sub(r"^(19|20)\d\d\s+", "", no_num)
    if no_year != no_num:
        variants.append(no_year)
    return variants


def card_key(title):
    """Stable key for deduping the same card across lots. Lowercase, synonym-
    fixed, number-normalized, punctuation-stripped. Year excluded (unreliable)."""
    q = ebay_query(title).lower()
    q = re.sub(r"^(19|20)\d\d\s+", "", q)
    q = re.sub(r"[^a-z0-9# ]+", " ", q)
    return _WS_RE.sub(" ", q).strip()
