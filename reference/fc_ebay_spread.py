#!/usr/bin/env python3
"""
FC -> eBay spread finder for pre-2010 Pokemon CGC 10.

Buys: Fanatics Collect AUCTION lots (sold prices, scraped from lot pages).
Sells: eBay (sold comps via Marketplace Insights, or active floor via Browse).

Credentials come from environment variables. Never hard-code them.

    export EBAY_CLIENT_ID='...'          # AppID
    export EBAY_CLIENT_SECRET='...'      # CertID

Usage:
    python fc_ebay_spread.py --check-access
    python fc_ebay_spread.py --card "1999 Base Set Charizard" --grade "CGC 10"
    python fc_ebay_spread.py --fc-url https://www.fanaticscollect.com/weekly/<uuid>/<slug>
    python fc_ebay_spread.py --batch cards.txt
"""

import os
import re
import sys
import json
import time
import base64
import argparse
import sqlite3
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------- constants

SANDBOX = os.environ.get("EBAY_SANDBOX", "").lower() in ("1", "true", "yes")
_HOST = "https://api.sandbox.ebay.com" if SANDBOX else "https://api.ebay.com"

EBAY_OAUTH = f"{_HOST}/identity/oauth2/token"
EBAY_INSIGHTS = f"{_HOST}/buy/marketplace_insights/v1_beta/item_sales/search"
EBAY_BROWSE = f"{_HOST}/buy/browse/v1/item_summary/search"
EBAY_TAXONOMY = f"{_HOST}/commerce/taxonomy/v1"

SCOPE_BASE = "https://api.ebay.com/oauth/api_scope"
SCOPE_INSIGHTS = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"

CCG_SINGLES_CATEGORY = "183454"      # CCG Individual Cards (verify via --find-category)
MARKETPLACE = "EBAY_US"
US_CATEGORY_TREE = "0"

# Per the Marketplace Insights spec: category_ids is REQUIRED (1-4 ids), and you
# must supply at least one of q / epid / gtin alongside it. Approved partners are
# given a specific list of categories they're allowed to query.

# Verified fee constants (Aug 2026)
FC_BUYERS_PREMIUM = 0.20             # flat, weekly + premier
EBAY_FVF = 0.1325                    # trading cards, non-Store, <= $7,500
EBAY_FVF_ABOVE = 0.0235              # portion above $7,500
EBAY_FVF_THRESHOLD = 7500.0
EBAY_PER_ORDER = 0.40
SHIP_COST = 12.00                    # label + signature (required >= $750) + packaging
SIGNATURE_THRESHOLD = 750.0

DB_PATH = os.environ.get("SPREAD_DB", "spread.db")


# ---------------------------------------------------------------- fee math

def ebay_net_proceeds(sale_price, ad_rate=0.0, store=False):
    """What actually lands in your pocket after eBay fees + shipping."""
    fvf_rate = 0.1235 if store else EBAY_FVF
    threshold = 2500.0 if store else EBAY_FVF_THRESHOLD

    if sale_price <= threshold:
        fvf = sale_price * fvf_rate
    else:
        fvf = threshold * fvf_rate + (sale_price - threshold) * EBAY_FVF_ABOVE

    fees = fvf + EBAY_PER_ORDER + (sale_price * ad_rate)
    return sale_price - fees - SHIP_COST


def breakeven_ebay_price(fc_cost, ad_rate=0.0, store=False):
    """Minimum eBay sale price to break even on an FC purchase. Binary search."""
    lo, hi = 0.0, max(fc_cost * 3, 100.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if ebay_net_proceeds(mid, ad_rate, store) < fc_cost:
            lo = mid
        else:
            hi = mid
    return hi


def max_fc_bid(target_ebay_price, margin=0.25, ad_rate=0.0, store=False):
    """Max you can pay on FC (incl. BP) and still hit `margin` net."""
    net = ebay_net_proceeds(target_ebay_price, ad_rate, store)
    return net / (1.0 + margin)


def hammer_from_total(total_incl_bp):
    """FC lot pages show price INCLUDING the 20% premium."""
    return total_incl_bp / (1.0 + FC_BUYERS_PREMIUM)


# ---------------------------------------------------------------- ebay auth

def get_token(scopes):
    cid = os.environ.get("EBAY_CLIENT_ID")
    secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("ERROR: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET environment variables.")

    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = requests.post(
        EBAY_OAUTH,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": " ".join(scopes)},
        timeout=30,
    )
    if r.status_code != 200:
        return None, f"{r.status_code} {r.text[:400]}"
    return r.json().get("access_token"), None


def check_access():
    """Determine which eBay APIs this account can actually use."""
    print("=" * 68)
    print("eBay API access check")
    print("=" * 68)

    tok, err = get_token([SCOPE_BASE])
    if not tok:
        print(f"  Browse API token   : FAIL  ({err})")
        print("\n  Check your keys are PRODUCTION (not sandbox) and the app is enabled.")
        return
    print("  Browse API token   : OK    (active listings available)")

    tok2, err2 = get_token([SCOPE_INSIGHTS])
    if not tok2:
        print(f"  Marketplace Insights: DENIED")
        print(f"      -> {err2[:200]}")
        print("\n  No sold-data API access. Options:")
        print("    1. Apply at developer.ebay.com (Limited Release; often denied)")
        print("    2. Use Terapeak in Seller Hub - FREE with your seller account,")
        print("       3 YEARS of sold data + sell-through %. Manual, but deepest.")
        print("    3. Commercial scraper API (SoldComps ~$29/mo, Apify ~$2.50/1k)")
        return

    r = requests.get(
        EBAY_INSIGHTS,
        headers={"Authorization": f"Bearer {tok2}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
        params={"q": "pokemon CGC 10", "category_ids": CCG_SINGLES_CATEGORY, "limit": "1"},
        timeout=30,
    )
    if r.status_code == 200:
        total = r.json().get("total", 0)
        print(f"  Marketplace Insights: OK    (sold data live, {total} matches, 90-day window)")
    else:
        print(f"  Marketplace Insights: token OK but call failed {r.status_code}")
        print(f"      -> {r.text[:300]}")


# ---------------------------------------------------------------- ebay data

def find_category(term="pokemon trading card"):
    """
    Taxonomy API - resolve the correct category_ids. Insights REQUIRES them and
    approved partners get a fixed allow-list, so guessing 183454 is not enough.
    """
    tok, err = get_token([SCOPE_BASE])
    if not tok:
        return None, err
    r = requests.get(
        f"{EBAY_TAXONOMY}/category_tree/{US_CATEGORY_TREE}/get_category_suggestions",
        headers={"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
        params={"q": term}, timeout=30,
    )
    if r.status_code != 200:
        return None, f"{r.status_code} {r.text[:200]}"

    out = []
    for s in r.json().get("categorySuggestions", []) or []:
        c = s.get("category", {}) or {}
        path = " > ".join(
            a.get("category", {}).get("categoryName", "")
            for a in reversed(s.get("categoryTreeNodeAncestors", []) or [])
        )
        out.append({"id": c.get("categoryId"), "name": c.get("categoryName"), "path": path})
    return out, None


def _grade_aspect_filter(grader="CGC", grade="10"):
    """
    eBay graded-card listings carry STRUCTURED aspects. Filtering on them beats
    regexing titles - it's how you avoid mislabeled and ambiguous listings.
    """
    return f"Professional Grader:{{{grader}}},Grade:{{{grade}}}"


def ebay_sold(query, limit=200, category=CCG_SINGLES_CATEGORY,
              grader=None, grade=None, days=90, epid=None):
    """
    Sold comps from Marketplace Insights. 90-day window is a hard eBay limit.
    Uses aspect_filter for grader/grade when available, and a lastSoldDate
    bound so results are date-scoped rather than whatever comes back.
    """
    tok, err = get_token([SCOPE_INSIGHTS])
    if not tok:
        return None, f"no Marketplace Insights access: {err[:150]}"

    cutoff = datetime.now(timezone.utc) - timedelta(days=min(days, 90))
    filters = [f"lastSoldDate:[{cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')}..]"]

    base = {
        "category_ids": category,
        "limit": "200",
        "filter": ",".join(filters),
    }
    if epid:
        base["epid"] = epid          # exact catalog match - beats keyword soup
    else:
        base["q"] = query
    if grader and grade:
        base["aspect_filter"] = _grade_aspect_filter(grader, grade)

    out, offset = [], 0
    while offset < limit:
        p = dict(base, offset=str(offset), limit=str(min(200, limit - offset)))
        r = requests.get(
            EBAY_INSIGHTS,
            headers={"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
            params=p, timeout=30,
        )
        if r.status_code != 200:
            # aspect_filter is unsupported in some categories - retry without it
            if "aspect_filter" in p and r.status_code in (400, 500):
                p.pop("aspect_filter")
                base.pop("aspect_filter", None)
                r = requests.get(
                    EBAY_INSIGHTS,
                    headers={"Authorization": f"Bearer {tok}",
                             "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
                    params=p, timeout=30)
            if r.status_code != 200:
                return None, f"{r.status_code} {r.text[:200]}"

        items = r.json().get("itemSales", []) or []
        if not items:
            break
        for it in items:
            price = it.get("lastSoldPrice", {}) or {}
            out.append({
                "title": it.get("title", ""),
                "price": float(price.get("value", 0) or 0),
                "currency": price.get("currency", "USD"),
                "sold_date": it.get("lastSoldDate", ""),
                "condition": it.get("condition", ""),
                "item_id": it.get("itemId", ""),
                # velocity signal straight from eBay - no inference needed
                "qty_sold": it.get("totalSoldQuantity", 1),
                "bid_count": it.get("bidCount"),
                "epid": it.get("epid", ""),
            })
        offset += len(items)
        if offset >= 10000:              # hard API ceiling
            break
        time.sleep(0.2)
    return out, None


def ebay_active_floor(query, limit=100, category=CCG_SINGLES_CATEGORY):
    """Cheapest current BIN — your undercut target."""
    tok, err = get_token([SCOPE_BASE])
    if not tok:
        return None, err

    r = requests.get(
        EBAY_BROWSE,
        headers={"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
        params={
            "q": query,
            "category_ids": category,
            "filter": "buyingOptions:{FIXED_PRICE}",
            "sort": "price",
            "limit": str(limit),
        },
        timeout=30,
    )
    if r.status_code != 200:
        return None, f"{r.status_code} {r.text[:200]}"

    out = []
    for it in r.json().get("itemSummaries", []) or []:
        price = it.get("price", {}) or {}
        out.append({
            "title": it.get("title", ""),
            "price": float(price.get("value", 0) or 0),
            "item_id": it.get("itemId", ""),
            "url": it.get("itemWebUrl", ""),
        })
    return out, None


# ------------------------------------------------------- grade normalization

PRISTINE_RE = re.compile(r"\b(pristine|p10|10\s*pristine)\b", re.I)
GEM_RE = re.compile(r"\b(gem\s*mint|gem)\b", re.I)
CGC10_RE = re.compile(r"\bCGC\s*10\b", re.I)
PSA10_RE = re.compile(r"\bPSA\s*10\b", re.I)


def classify_grade(title):
    """CGC 10 Pristine and Gem Mint are DIFFERENT markets. Never pool them."""
    t = title or ""
    if CGC10_RE.search(t):
        if PRISTINE_RE.search(t):
            return "CGC10_PRISTINE"
        if GEM_RE.search(t):
            return "CGC10_GEM"
        return "CGC10_UNSPEC"
    if PSA10_RE.search(t):
        return "PSA10"
    return "OTHER"


def summarize(sales, want="CGC10_GEM"):
    """Trimmed, recency-weighted stats for one grade tier."""
    rows = [s for s in sales if classify_grade(s["title"]) == want and s["price"] > 0]
    if not rows:
        return None
    rows.sort(key=lambda r: r["sold_date"] or "", reverse=True)
    prices = sorted(r["price"] for r in rows)

    if len(prices) >= 5:                      # trim outliers
        prices = prices[1:-1]

    n = len(prices)
    median = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
    recent = [r["price"] for r in rows[:3]]

    return {
        "grade": want,
        "n_sales_90d": len(rows),
        "median": round(median, 2),
        "mean": round(sum(prices) / n, 2),
        "low": min(prices),
        "high": max(prices),
        "last_3_avg": round(sum(recent) / len(recent), 2),
        "last_sold": rows[0]["sold_date"][:10] if rows[0]["sold_date"] else "",
    }


# ------------------------------------------------------------ fanatics side

LOT_RE = re.compile(r"https?://www\.fanaticscollect\.com/(weekly|premier)/[0-9a-f\-]+/[^\s\"'<>]+", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")


def fetch_fc_lot(url, session=None):
    """
    Fanatics lot pages are server-rendered — price, bids, and status are in the HTML.
    robots.txt permits /weekly/. Be polite: low rate, identify yourself.
    """
    s = session or requests.Session()
    r = s.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; card-research/1.0)"}, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"

    html = r.text
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    title_m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    title = (title_m.group(1) if title_m else "").split(" on Fanatics")[0].strip()

    # price nearest the "W/ Buyer's Premium" marker
    total = None
    bp_idx = text.lower().find("buyer's premium")
    if bp_idx > 0:
        window = text[max(0, bp_idx - 200): bp_idx]
        hits = PRICE_RE.findall(window)
        if hits:
            total = float(hits[-1].replace(",", ""))
    if total is None:
        hits = PRICE_RE.findall(text)
        if hits:
            total = float(hits[0].replace(",", ""))

    bids_m = re.search(r"(\d+)\s*[Bb]ids?", text)
    lot_m = re.search(r"(WA\d+\s*Lot:?\s*\w+|Lot:?\s*\w+)", text)
    sold_m = re.search(r"Sold:?\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})", text)
    year_m = re.search(r"\b(19[89]\d|20[0-2]\d)\b", title)

    return {
        "url": url,
        "title": title,
        "grade_class": classify_grade(title),
        "total_incl_bp": total,
        "hammer": round(hammer_from_total(total), 2) if total else None,
        "bids": int(bids_m.group(1)) if bids_m else None,
        "lot": lot_m.group(1).strip() if lot_m else None,
        "sold_date": sold_m.group(1) if sold_m else None,
        "year": int(year_m.group(1)) if year_m else None,
        "is_auction": "/weekly/" in url or "/premier/" in url,
    }, None


# ------------------------------------------------------------------ scoring

def score_opportunity(fc_cost, ebay_stats, floor_price=None, ad_rate=0.0, store=False):
    """
    Velocity is a GATE, not a score term. Zero recent sales = do not buy,
    no matter how large the discount looks.
    """
    if not ebay_stats:
        return {"verdict": "NO_COMPS", "reason": "no eBay sold data for this grade tier"}

    n = ebay_stats["n_sales_90d"]
    market = ebay_stats["last_3_avg"]

    if n < 3:
        return {
            "verdict": "ILLIQUID",
            "reason": f"only {n} sold in 90 days - high risk of dead inventory",
            "market": market, "n_sales_90d": n,
        }

    net = ebay_net_proceeds(market, ad_rate, store)
    profit = net - fc_cost
    margin = profit / fc_cost if fc_cost else 0
    be = breakeven_ebay_price(fc_cost, ad_rate, store)

    result = {
        "market_last3": market,
        "median_90d": ebay_stats["median"],
        "n_sales_90d": n,
        "last_sold": ebay_stats["last_sold"],
        "fc_cost": round(fc_cost, 2),
        "net_if_sold_at_market": round(net, 2),
        "profit": round(profit, 2),
        "margin_pct": round(margin * 100, 1),
        "breakeven_ebay_price": round(be, 2),
        "max_bid_for_25pct": round(max_fc_bid(market, 0.25, ad_rate, store), 2),
    }

    if floor_price:
        undercut = floor_price * 0.97
        net_uc = ebay_net_proceeds(undercut, ad_rate, store)
        result["current_floor"] = floor_price
        result["net_if_undercut_floor"] = round(net_uc, 2)
        result["exit_ok"] = net_uc >= fc_cost * 1.10

    if margin >= 0.25:
        result["verdict"] = "BUY"
    elif margin >= 0.10:
        result["verdict"] = "THIN"
    else:
        result["verdict"] = "PASS"
    return result


# ---------------------------------------------------------------- storage

def init_db(path=DB_PATH):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS ebay_sales(
        item_id TEXT PRIMARY KEY, query TEXT, title TEXT, grade_class TEXT,
        price REAL, sold_date TEXT, captured_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS fc_lots(
        url TEXT PRIMARY KEY, title TEXT, grade_class TEXT, total_incl_bp REAL,
        hammer REAL, bids INTEGER, lot TEXT, sold_date TEXT, year INTEGER,
        captured_at TEXT)""")
    con.commit()
    return con


def archive_sales(con, query, sales):
    """eBay only exposes 90 days. Archive weekly and you build history nobody sells."""
    now = datetime.now(timezone.utc).isoformat()
    for s in sales:
        con.execute(
            "INSERT OR IGNORE INTO ebay_sales VALUES (?,?,?,?,?,?,?)",
            (s["item_id"], query, s["title"], classify_grade(s["title"]),
             s["price"], s["sold_date"], now))
    con.commit()


# ------------------------------------------------------------------- main

def run_card(card, grade_label="CGC 10", fc_cost=None, ad_rate=0.0, store=False,
             category=CCG_SINGLES_CATEGORY, days=90):
    query = f"{card} {grade_label}"
    print(f"\n{'='*68}\n{query}\n{'='*68}")

    m = re.match(r"\s*(CGC|PSA|BGS|SGC)\s*([\d.]+)", grade_label, re.I)
    grader = m.group(1).upper() if m else None
    gnum = m.group(2) if m else None

    sales, err = ebay_sold(query, category=category, grader=grader, grade=gnum, days=days)
    if err:
        print(f"  sold data unavailable: {err}")
        print("  -> falling back to ACTIVE listings (floor only, not market value)")
        active, aerr = ebay_active_floor(query)
        if aerr:
            print(f"  active lookup failed too: {aerr}")
            return
        cgc = [a for a in active if classify_grade(a["title"]).startswith("CGC10")]
        if cgc:
            cheapest = min(cgc, key=lambda x: x["price"])
            print(f"  cheapest active CGC 10 BIN: ${cheapest['price']:,.2f}")
            print(f"    {cheapest['title'][:70]}")
            print(f"    {cheapest['url']}")
            print(f"  active CGC 10 supply: {len(cgc)}")
        else:
            print(f"  no active CGC 10 listings found ({len(active)} results scanned)")
        return

    con = init_db()
    archive_sales(con, query, sales)

    for tier in ("CGC10_GEM", "CGC10_PRISTINE", "CGC10_UNSPEC", "PSA10"):
        st = summarize(sales, tier)
        if st:
            print(f"  {tier:16s} n={st['n_sales_90d']:3d}  median ${st['median']:>10,.2f}  "
                  f"last3 ${st['last_3_avg']:>10,.2f}  last {st['last_sold']}")

    gem = summarize(sales, "CGC10_GEM") or summarize(sales, "CGC10_UNSPEC")
    if fc_cost and gem:
        active, _ = ebay_active_floor(query)
        floor = None
        if active:
            c = [a["price"] for a in active if classify_grade(a["title"]).startswith("CGC10")]
            floor = min(c) if c else None
        print("\n  " + json.dumps(score_opportunity(fc_cost, gem, floor, ad_rate, store), indent=2)
              .replace("\n", "\n  "))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-access", action="store_true", help="test which eBay APIs you can use")
    ap.add_argument("--find-category", metavar="TERM",
                    help='resolve category_ids via Taxonomy API, e.g. "pokemon card"')
    ap.add_argument("--card", help='e.g. "1999 Base Set Charizard"')
    ap.add_argument("--grade", default="CGC 10")
    ap.add_argument("--category", default=CCG_SINGLES_CATEGORY, help="eBay category id")
    ap.add_argument("--days", type=int, default=90, help="lookback window (max 90)")
    ap.add_argument("--fc-cost", type=float, help="what you'd pay on FC incl. 20%% premium")
    ap.add_argument("--fc-url", help="Fanatics lot URL to pull the real sold price from")
    ap.add_argument("--batch", help="file of card names, one per line")
    ap.add_argument("--ad-rate", type=float, default=0.0, help="promoted listings rate e.g. 0.05")
    ap.add_argument("--store", action="store_true", help="you have an eBay Basic Store (12.35%%)")
    args = ap.parse_args()

    if args.check_access:
        check_access()
        return

    if args.find_category:
        cats, err = find_category(args.find_category)
        if err:
            print(f"taxonomy lookup failed: {err}")
            return
        for c in cats:
            print(f"  {c['id']:>10}  {c['name']}")
            if c["path"]:
                print(f"              {c['path']}")
        return

    if args.fc_url:
        lot, err = fetch_fc_lot(args.fc_url)
        if err:
            print(f"FC fetch failed: {err}")
            return
        print(json.dumps(lot, indent=2))
        if lot["total_incl_bp"] and lot["title"]:
            run_card(lot["title"], args.grade, lot["total_incl_bp"], args.ad_rate, args.store)
        return

    if args.batch:
        with open(args.batch) as fh:
            for line in fh:
                if line.strip() and not line.startswith("#"):
                    run_card(line.strip(), args.grade, args.fc_cost, args.ad_rate, args.store, args.category, args.days)
                    time.sleep(0.5)
        return

    if args.card:
        run_card(args.card, args.grade, args.fc_cost, args.ad_rate, args.store, args.category, args.days)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
