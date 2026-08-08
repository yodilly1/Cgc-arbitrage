"""Fanatics Collect ingestion.

Strategy (verified live, Aug 2026):
  1. https://www.fanaticscollect.com/sitemap.xml lists gzipped sub-sitemaps.
     `weekly-auction-*.xml.gz` = the CURRENT weekly auction's lot URLs.
  2. Individual lot pages are server-rendered. Each embeds:
       - a Next.js flight payload with a structured `prefetchedItemData`
         object: title, currentBid (hammer, in cents), auction shortName /
         endsAt / status, lotString, isClosed — the authoritative source.
       - a JSON-LD Product block: price INCLUDING the 20% premium,
         availability (InStock/OutOfStock).
       - rendered text with "Bids N" and "Sold: <date>".
  3. robots.txt permits /weekly/. Be polite anyway: identify the client,
     rate-limit, cache closed lots forever (they never change).

TRAP: URL slugs are recycled and can disagree with page content. Slugs are
used only as a cheap pre-filter; every included lot is re-verified from the
parsed page title.
"""

import re
import gzip
import json
import time
import threading

import requests

from . import grading

BASE = "https://www.fanaticscollect.com"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"
USER_AGENT = "Mozilla/5.0 (compatible; cgc-arbitrage-research/1.0)"

_LOC_RE = re.compile(r"<loc>(.*?)</loc>")
_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_BIDS_RE = re.compile(r"\bBids?\s*:?\s*(\d+)\b|\b(\d+)\s*Bids?\b")
_SOLD_RE = re.compile(r"Sold\s*:?\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})")


class FanaticsClient:
    def __init__(self, min_interval=1.5, session=None):
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()       # shared instance across a pool

    def _throttle(self):
        # Serialize the spacing so N pool workers don't all fire at once
        # (Cloudflare ban risk takes down the whole pipeline, not one call).
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _get(self, url, timeout=30, retries=3):
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.s.get(url, timeout=timeout)
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code in (429, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 5)
                continue
            return r
        return r

    # ------------------------------------------------------------- sitemaps

    def weekly_sitemap_urls(self):
        r = self._get(SITEMAP_INDEX)
        r.raise_for_status()
        return [u for u in _LOC_RE.findall(r.text)
                if re.search(r"/sitemap/weekly-auction-\d+\.xml\.gz$", u)]

    def history_sitemap_urls(self):
        """sales-history-weekly-auction-*.xml.gz — every closed weekly lot."""
        r = self._get(SITEMAP_INDEX)
        r.raise_for_status()
        return [u for u in _LOC_RE.findall(r.text)
                if re.search(r"/sitemap/sales-history-weekly-auction-\d+\.xml\.gz$", u)]

    def _sitemap_lot_urls(self, sitemaps):
        urls = []
        for sm in sitemaps:
            r = self._get(sm, timeout=90)
            r.raise_for_status()
            body = r.content
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            urls.extend(_LOC_RE.findall(body.decode("utf-8", "replace")))
        return urls

    def lot_urls(self):
        """All lot URLs in the current weekly auction sitemaps."""
        return self._sitemap_lot_urls(self.weekly_sitemap_urls())

    def history_lot_urls(self):
        """All closed-lot URLs across the sales-history sitemaps (millions;
        callers slug-filter before fetching pages)."""
        return self._sitemap_lot_urls(self.history_sitemap_urls())

    # ------------------------------------------------------------ lot pages

    def fetch_lot(self, url):
        """Fetch and parse one lot page. Returns (dict, None) or (None, err)."""
        r = self._get(url)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        try:
            return parse_lot_html(r.text, url), None
        except Exception as e:  # noqa: BLE001 - one bad page must not kill a scan
            return None, f"parse error: {e}"


# ------------------------------------------------------------------ parsing

def _flight_text(html):
    """Reassemble the Next.js flight payload from its script chunks."""
    parts = []
    for lit in _FLIGHT_RE.findall(html):
        try:
            parts.append(json.loads(lit))
        except ValueError:
            continue
    return "".join(parts)


def _extract_json_object(text, anchor):
    """Brace-match the JSON object that follows `anchor` in `text`."""
    i = text.find(anchor)
    if i < 0:
        return None
    i = text.find("{", i)
    if i < 0:
        return None
    depth, j, in_str, esc = 0, i, False, False
    while j < len(text):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except ValueError:
                        return None
        j += 1
    return None


def parse_lot_html(html, url):
    flight = _flight_text(html)
    item = _extract_json_object(flight, '"prefetchedItemData":')

    # JSON-LD fallback / cross-check (price here INCLUDES the 20% premium)
    ld = None
    for raw in _LDJSON_RE.findall(html):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if d.get("@type") == "Product":
            ld = d
            break

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    title = (item or {}).get("title") or (ld or {}).get("name") or ""
    auction = (item or {}).get("auction") or {}
    states = (item or {}).get("states") or {}
    bid = ((item or {}).get("currentBid") or {}).get("amountInCents")
    offers = (ld or {}).get("offers") or {}

    bids_m = _BIDS_RE.search(text)
    sold_m = _SOLD_RE.search(text)

    is_closed = bool(states.get("isClosed")) or \
        offers.get("availability", "").endswith("OutOfStock")

    # Price semantics differ by lot state (verified against live pages):
    #   ACTIVE: JSON-LD price == current bid (hammer, NO premium)
    #   CLOSED: JSON-LD price == final price W/ 20% premium
    # The flight currentBid is ALWAYS the hammer. Prefer it; derive the rest.
    hammer = round(bid / 100.0, 2) if bid is not None else None
    ld_price = offers.get("price")
    if hammer is None and ld_price is not None:
        hammer = round(float(ld_price) / 1.20, 2) if is_closed else float(ld_price)
    price_incl_bp = round(hammer * 1.20, 2) if hammer is not None else None

    listing_type = (item or {}).get("listingType") or (
        "WEEKLY" if "/weekly/" in url else
        "PREMIER" if "/premier/" in url else "OTHER")

    return {
        "uuid": (item or {}).get("id") or (ld or {}).get("sku") or url.split("/")[-2],
        "url": url,
        "title": title,
        "listing_type": listing_type,
        "is_auction": listing_type in ("WEEKLY", "PREMIER"),
        "hammer": hammer,
        "price_incl_bp": float(price_incl_bp) if price_incl_bp is not None else None,
        "bids": int(next(g for g in bids_m.groups() if g)) if bids_m else None,
        "lot_string": (item or {}).get("lotString"),
        "auction_name": auction.get("shortName") or auction.get("name"),
        "auction_ends_at": auction.get("endsAt"),
        "auction_status": auction.get("status"),
        "is_closed": is_closed,
        "sold_date": sold_m.group(1) if sold_m else None,
        # page-title-derived attributes (slugs lie; titles are authoritative)
        "grade_class": grading.classify_grade(title),
        "year": grading.extract_year(title),
        "language": grading.detect_language(title),
        "product_line": grading.detect_product_line(title),
        "is_pokemon": grading.is_pokemon(title),
    }


# ------------------------------------------------------------- slug matching

_SLUG_GRADE_RE = re.compile(r"-(cgc|psa|bgs|sgc)-.*$")
_SLUG_YEAR_RE = re.compile(r"^(19|20)\d\d-")


def slug_card_key(url):
    """Card identity key derived from an FC slug, grade/year stripped.

    Both live and historical URLs use FC's own slug vocabulary, so matching
    history to live lots on this key is self-consistent (unlike matching FC
    text to eBay text). Slugs are occasionally recycled (trap #2) — always
    verify the fetched page's parsed title before trusting a match.
    """
    slug = url.rstrip("/").split("/")[-1].lower()
    slug = _SLUG_YEAR_RE.sub("", slug)
    slug = _SLUG_GRADE_RE.sub("", slug)
    return slug.strip("-")


def slug_is_cgc10(url):
    slug = url.rstrip("/").split("/")[-1].lower()
    return "cgc-10" in slug or "cgc-pristine-10" in slug


# ------------------------------------------------------------ slug prefilter

def slug_candidates(urls, max_year=2009):
    """Cheap slug-based pre-filter: Pokemon + CGC 10 + year <= max_year.

    Slugs can be stale (trap #2), so this only bounds how many pages we fetch;
    final inclusion is decided from the parsed page title.
    """
    out = []
    for u in urls:
        slug = u.rstrip("/").split("/")[-1].lower()
        if "pokemon" not in slug:
            continue
        if "cgc-10" not in slug and "cgc-pristine" not in slug:
            continue
        m = re.match(r"(\d{4})-", slug)
        if not m or int(m.group(1)) > max_year:
            continue
        out.append(u)
    return out
