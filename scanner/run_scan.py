"""Orchestrator: ingest FC lots -> eBay comps -> velocity gate -> score -> report.

Degrades gracefully by credential level:
  - no eBay keys:      FC-only scan (lot list + bid-count demand proxy)
  - Browse only:       adds active floor + supply depth
  - + Insights:        full sold comps, velocity gate, max-bid math

Usage:
  python -m scanner.run_scan                    # full weekly scan
  python -m scanner.run_scan --max-lots 25      # smoke test
  python -m scanner.run_scan --check-access     # probe eBay API access
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

from . import archive, ebay, fanatics, normalize, report, scoring


def check_access():
    c = ebay.EbayClient()
    res = c.check_access()
    print("eBay API access:")
    print(f"  credentials set : {res['credentials']}")
    print(f"  Browse (active) : {'OK' if res['browse'] else 'NO'}")
    print(f"  Insights (sold) : {'OK' if res['insights'] else 'NO'}")
    for k, v in res["detail"].items():
        print(f"    {k}: {v}")
    if res["credentials"] and not res["insights"]:
        print("\nNo sold-data API. Fallbacks, in order of value:")
        print("  1. Terapeak (Seller Hub -> Research): free, 3y history, manual")
        print("  2. SoldComps (~$29/mo) or Apify ebay-sold-listings (~$2.50/1k)")
    return res


def scan(max_lots=None, db_path=None, out_dir="reports", target_margin=0.25,
         ad_rate=0.0, store=False, skip_ebay=False, max_year=2009):
    con = archive.connect(db_path)
    fc = fanatics.FanaticsClient()
    ec = ebay.EbayClient()
    notes = []

    access = {"browse": False, "insights": False}
    if skip_ebay or not ec.has_credentials:
        comps_source = "none (no eBay credentials)"
        notes.append("No eBay API keys configured — comps and velocity gate unavailable. "
                     "Rows show FC data only; bid count is the only demand signal.")
    else:
        access = ec.check_access()
        if access["insights"]:
            comps_source = "eBay Marketplace Insights (90d sold)"
        elif access["browse"]:
            comps_source = "eBay Browse (active floor only — NO sold data)"
            notes.append("Marketplace Insights denied — showing active floor only. "
                         "Floor is an asking price, not market value.")
        else:
            comps_source = "none (eBay auth failed)"
            notes.append(f"eBay auth failed: {access['detail']}")

    # ---- 1. ingest ---------------------------------------------------------
    print("Fetching Fanatics weekly-auction sitemaps ...", flush=True)
    urls = fc.lot_urls()
    candidates = fanatics.slug_candidates(urls, max_year=max_year)
    print(f"  {len(urls)} lots in auction, {len(candidates)} pre-2010 Pokemon CGC10 candidates")
    if max_lots:
        candidates = candidates[:max_lots]

    lots, errors = [], 0
    for i, u in enumerate(candidates, 1):
        uuid = u.rstrip("/").split("/")[-2]
        cached = archive.known_closed_lot(con, uuid)
        if cached:
            lots.append(cached)
            continue
        lot, err = fc.fetch_lot(u)
        if err:
            errors += 1
            if errors <= 5:
                print(f"  ! {u}: {err}")
            continue
        archive.archive_lot(con, lot)
        lots.append(lot)
        if i % 50 == 0:
            con.commit()
            print(f"  fetched {i}/{len(candidates)}", flush=True)
    con.commit()

    # Page-title verification (slugs lie): re-check scope on parsed titles.
    live = [l for l in lots
            if l["is_auction"] and not l["is_closed"] and l["is_pokemon"]
            and l["grade_class"].startswith("CGC10")
            and (l["year"] is None or l["year"] <= max_year)]
    auction_name = next((l["auction_name"] for l in live if l["auction_name"]), "")
    print(f"  {len(live)} live in-scope lots ({errors} fetch errors)")

    # ---- 2-4. comps / velocity / floor ------------------------------------
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    comp_cache, floor_cache = {}, {}
    scored = []
    for lot in live:
        key = normalize.card_key(lot["title"])
        stats = floor = supply = None

        if access["insights"] and key not in comp_cache:
            sales = None
            for q in normalize.query_variants(lot["title"]):
                sales, err = ec.sold(q, grader="CGC", grade="10")
                if err:
                    notes_msg = f"Insights error on '{q[:60]}': {err}"
                    if notes_msg not in notes:
                        notes.append(notes_msg)
                    break
                if sales:
                    archive.archive_sales(con, q, sales)
                    break
                time.sleep(0.3)
            comp_cache[key] = sales or []
        if access["insights"]:
            stats = scoring.summarize(comp_cache.get(key, []), lot["grade_class"])
            if stats:
                archive.record_comp_stats(con, run_at, key, lot["grade_class"], stats)

        if access["browse"]:
            if key not in floor_cache:
                q = normalize.ebay_query(lot["title"]) + " CGC 10"
                active, _err = ec.active_listings(q)
                if active:
                    from . import grading
                    same = [a["price"] for a in active
                            if grading.classify_grade(a["title"]) == lot["grade_class"]]
                    floor_cache[key] = (min(same), len(same)) if same else (None, 0)
                else:
                    floor_cache[key] = (None, None)
                time.sleep(0.3)
            floor, supply = floor_cache[key]

        s = scoring.score_lot(lot, stats, floor, supply,
                              target_margin=target_margin, ad_rate=ad_rate, store=store)
        if not access["insights"]:
            s["verdict"] = "NO_DATA"
            s["reason"] = "no sold-comp source configured"
            s["floor"] = floor
            s["active_supply"] = supply
        scored.append(s)
    con.commit()

    scored.sort(key=scoring.rank_key)

    # ---- 6. deliver --------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    html_doc = report.to_html(scored, auction_name, comps_source, notes=notes)
    for name in (f"scan-{stamp}.html", "latest.html", "index.html"):
        with open(os.path.join(out_dir, name), "w") as fh:
            fh.write(html_doc)
    with open(os.path.join(out_dir, f"scan-{stamp}.csv"), "w") as fh:
        fh.write(report.to_csv(scored))
    with open(os.path.join(out_dir, "latest.json"), "w") as fh:
        json.dump({"generated_at": run_at, "auction": auction_name,
                   "comps_source": comps_source, "lots": scored}, fh, indent=1)

    archive.record_scan(con, auction_name, len(live), len(scored), comps_source,
                        "; ".join(notes))
    n_bid = sum(1 for s in scored if s.get("verdict") == "BID")
    print(f"\nDone: {len(scored)} lots scored, {n_bid} BID candidates.")
    print(f"Report: {out_dir}/latest.html")
    return scored


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-access", action="store_true")
    ap.add_argument("--max-lots", type=int, help="cap lot fetches (smoke test)")
    ap.add_argument("--skip-ebay", action="store_true", help="FC-only scan")
    ap.add_argument("--db", default=None, help="sqlite path (default data/archive.sqlite)")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--margin", type=float, default=0.25, help="target net margin")
    ap.add_argument("--ad-rate", type=float, default=0.0, help="promoted listings rate")
    ap.add_argument("--store", action="store_true", help="eBay Basic Store fee schedule")
    ap.add_argument("--max-year", type=int, default=2009)
    args = ap.parse_args(argv)

    if args.check_access:
        check_access()
        return 0

    scan(max_lots=args.max_lots, db_path=args.db, out_dir=args.out,
         target_margin=args.margin, ad_rate=args.ad_rate, store=args.store,
         skip_ebay=args.skip_ebay, max_year=args.max_year)
    return 0


if __name__ == "__main__":
    sys.exit(main())
