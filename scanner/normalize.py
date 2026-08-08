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


def comp_filter(fc_title, sales):
    """Keep only sales that are plausibly the SAME card as the FC lot.

    Keyword search is loose: a query for Fossil Dragonite #19 returns Fossil
    Dragonite #4 holo sales, Japanese results pollute English comps, and 1st
    Edition/Unlimited get mixed. Wrong comps are worse than no comps, so the
    filter is strict: subject name + card number + edition/shadowless/
    language agreement all required.
    """
    from . import grading

    q = ebay_query(fc_title).lower()
    m = re.search(r"#(\d+)", q)
    num = (m.group(1).lstrip("0") or "0") if m else None
    if m:
        pre = q[:m.start()].split()
        subject = pre[-1] if pre else None
    else:
        words = q.split()
        subject = words[-1] if words else None
    first_ed = "1st" in q
    shadowless = "shadowless" in q
    error_var = "error" in q
    japanese = grading.detect_language(fc_title) == "JA"

    # Grade tokens must not leak into card-number matching: "CGC 10" would
    # satisfy a #10 (or #1) card-number check on EVERY comp title.
    _grade_tokens = re.compile(r"\b(cgc|psa|bgs|sgc)\b[^0-9]*\d+(\.\d+)?", re.I)

    out = []
    for s in sales:
        t = (s.get("title") or "").lower()
        if subject and not re.search(rf"\b{re.escape(subject)}\b", t):
            continue                        # word boundary: 'mew' != 'mewtwo'
        if num:
            t_nograde = _grade_tokens.sub(" ", t)
            # allow zero-padding both ways: #6 matches '#006' and vice versa
            if not re.search(rf"(?<!\d)0*{num}(?!\d)", t_nograde):
                continue
        if first_ed != ("1st" in t or "first ed" in t):
            continue
        if shadowless != ("shadowless" in t):
            continue
        if error_var != ("error" in t):     # error variants price differently
            continue
        if japanese != (grading.detect_language(t) == "JA"):
            continue                        # symmetric: set-name hints count on both sides
        out.append(s)
    return out


def card_key(title):
    """Stable key for deduping the same card across lots. Lowercase, synonym-
    fixed, number-normalized, punctuation-stripped. Year excluded (unreliable)."""
    q = ebay_query(title).lower()
    q = re.sub(r"^(19|20)\d\d\s+", "", q)
    q = re.sub(r"[^a-z0-9# ]+", " ", q)
    return _WS_RE.sub(" ", q).strip()
