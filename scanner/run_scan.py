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
import concurrent.futures
from datetime import datetime, timezone

from . import archive, ebay, fanatics, fees, normalize, report, scoring, soldcomps


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


def scan(max_lots=None, db_path=None, out_dir="reports", target_margin=0.20,
         ad_rate=0.0, store=False, skip_ebay=False, max_year=2009,
         fc_history_cap=300):
    con = archive.connect(db_path)
    fc = fanatics.FanaticsClient()
    ec = ebay.EbayClient()
    sc = soldcomps.SoldCompsClient()
    notes = []

    access = {"browse": False, "insights": False}
    if not skip_ebay and ec.has_credentials:
        access = ec.check_access()
        if not access["browse"]:
            notes.append(f"eBay auth failed: {access['detail']}")

    # Sold-comp provider chain: Insights (official) > SoldComps (commercial)
    if access["insights"]:
        comps_mode, comps_source = "insights", "eBay Marketplace Insights (90d sold)"
    elif sc.has_key and not skip_ebay:
        comps_mode, comps_source = "soldcomps", "SoldComps (90d sold, scraped)"
    else:
        comps_mode, comps_source = None, "none (no sold-data source)"
        notes.append("No sold-comp source configured — velocity gate unavailable. "
                     "Rows show FC data" +
                     (" + active floor" if access["browse"] else "") +
                     "; bid count is the main demand signal.")

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
        cached = archive.cached_lot(con, uuid)
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

    # ---- 2b. Fanatics own comps (sales-history sitemaps) -------------------
    # What each card actually clears for ON FC — the acquisition-market comp
    # (alt.xyz-style multi-marketplace view). Archived permanently; coverage
    # compounds every run. Bounded per run to keep scraping polite.
    fc_comps_by_key = {}
    if fc_history_cap > 0:
        try:
            print("Indexing FC sales history ...", flush=True)
            hist_index = {}
            for u in fc.history_lot_urls():
                slug = u.rstrip("/").split("/")[-1].lower()
                if "pokemon" in slug and fanatics.slug_is_cgc10(u):
                    hist_index.setdefault(fanatics.slug_card_key(u), []).append(u)
            known = archive.known_uuids(con)
            fetch_list, seen_urls = [], set()
            for lot in sorted(live, key=lambda l: -(l["price_incl_bp"] or 0)):
                for u in hist_index.get(fanatics.slug_card_key(lot["url"]), []):
                    if u.rstrip("/").split("/")[-2] not in known and u not in seen_urls:
                        seen_urls.add(u)
                        fetch_list.append(u)
            fetch_list = fetch_list[:fc_history_cap]
            print(f"  {len(fetch_list)} closed FC lots to fetch "
                  f"(cap {fc_history_cap})", flush=True)

            def _fetch_hist(u):
                return fanatics.FanaticsClient(min_interval=1.5).fetch_lot(u)

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                for i, (hlot, herr) in enumerate(ex.map(_fetch_hist, fetch_list), 1):
                    if hlot and hlot["is_closed"]:
                        archive.archive_lot(con, hlot)
                    if i % 50 == 0:
                        con.commit()
                        print(f"  history fetched {i}/{len(fetch_list)}", flush=True)
            con.commit()

            # Build per-card FC comp stats from EVERYTHING archived, with the
            # same same-card title verification used for eBay comps.
            pool = {}
            for row in archive.closed_cgc10_pokemon(con):
                pool.setdefault(fanatics.slug_card_key(row["url"]), []).append(row)
            for lot in live:
                k = fanatics.slug_card_key(lot["url"])
                cands = [r for r in pool.get(k, [])
                         if r["grade_class"] == lot["grade_class"]
                         and normalize.comp_filter(lot["title"], [{"title": r["title"]}])]
                st = scoring.fc_summarize(cands)
                if st:
                    # Key MUST include the tier — Pristine and Gem Mint are
                    # different markets and must never share FC stats.
                    fc_comps_by_key[(normalize.card_key(lot["title"]),
                                     lot["grade_class"])] = st
            print(f"  FC comps available for {len(fc_comps_by_key)} cards", flush=True)
        except Exception as e:  # noqa: BLE001 - FC comps are additive, not critical
            notes.append(f"FC sales-history comps unavailable this run: {e}")

    # ---- 2-4. comps / velocity / floor ------------------------------------
    # Most expensive lots first, so a capped comp quota covers the lots where
    # a mistake costs the most.
    live.sort(key=lambda l: -(l["price_incl_bp"] or 0))
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    quota_gone = False

    # Unique cards, preserving the price-priority order.
    unique = {}
    for lot in live:
        unique.setdefault(normalize.card_key(lot["title"]), lot["title"])

    # Comp prefetch. SoldComps live-scrapes eBay (~25s/request), so a serial
    # sweep of ~600 cards takes hours — run a small worker pool instead.
    # Cache hits are resolved in the main thread; only real HTTP fans out.
    comp_cache = {}

    def _fetch_comps(title):
        """Runs in a worker thread: HTTP only, no sqlite."""
        client = soldcomps.SoldCompsClient() if comps_mode == "soldcomps" else ec
        fetched = []            # (query, sales) pairs to archive in main thread
        for q in normalize.query_variants(title):
            if comps_mode == "soldcomps":
                q = f"{q} CGC 10"
                sales, err = client.sold(q)
            else:
                sales, err = client.sold(q, grader="CGC", grade="10")
            if err:
                return fetched, None, f"comp lookup error on '{q[:60]}': {err}"
            fetched.append((q, sales or []))
            if sales:
                return fetched, sales, None
            time.sleep(0.3)
        return fetched, [], None

    if comps_mode:
        todo = {}
        for key, title in unique.items():
            sales = None
            for q in normalize.query_variants(title):
                cq = f"{q} CGC 10" if comps_mode == "soldcomps" else q
                cached = archive.cached_sales(con, cq)
                if cached:
                    sales = cached
                    break
                if cached is not None:
                    sales = []          # cached zero-result; keep trying variants
                    continue
                sales = None            # uncached variant -> needs a fetch
                break
            if sales is None:
                todo[key] = title
            else:
                comp_cache[key] = sales
        print(f"  comps: {len(comp_cache)} cached, {len(todo)} to fetch", flush=True)

        workers = 4 if comps_mode == "soldcomps" else 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_comps, title): key
                    for key, title in todo.items()}
            done_n = 0
            for fut in concurrent.futures.as_completed(futs):
                key = futs[fut]
                try:
                    fetched, sales, err = fut.result()
                except soldcomps.QuotaExhausted:
                    if not quota_gone:
                        quota_gone = True
                        notes.append("SoldComps quota exhausted mid-scan — "
                                     "uncovered lots are labeled NO_DATA.")
                    for f in futs:
                        f.cancel()
                    continue
                for q, s_rows in fetched:
                    archive.archive_sales(con, q, s_rows, source=comps_mode)
                if err and err not in notes:
                    notes.append(err)
                else:
                    comp_cache[key] = sales or []
                done_n += 1
                if done_n % 25 == 0:
                    print(f"  comps fetched {done_n}/{len(todo)}", flush=True)

    # Floor prefetch (Browse is fast; a small pool still helps).
    floor_cache = {}
    if access["browse"]:
        from . import grading

        def _fetch_floor(item):
            key, title = item
            q = normalize.ebay_query(title) + " CGC 10"
            active, _err = ec.active_listings(q)
            return key, active or []

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            for key, active in ex.map(_fetch_floor, unique.items()):
                floor_cache[key] = active

    # FC -> eBay calibration ratio from cards priced in BOTH markets this run.
    pairs, paired_keys = [], set()
    for lot in live:
        key = normalize.card_key(lot["title"])
        fcst = fc_comps_by_key.get((key, lot["grade_class"]))
        if not fcst or (key, lot["grade_class"]) in paired_keys or key not in comp_cache:
            continue
        st = scoring.summarize(
            normalize.comp_filter(lot["title"], comp_cache[key]), lot["grade_class"])
        if st:
            paired_keys.add((key, lot["grade_class"]))
            pairs.append((min(fcst["last_3_avg"], fcst["median"]),
                          scoring.comp_value(st)))
    fc_ratio = scoring.fc_calibration_ratio(pairs)
    if pairs:
        notes.append(f"FC→eBay calibration ratio {fc_ratio} "
                     f"(from {len(pairs)} cards sold in both markets).")

    scored = []
    for lot in live:
        key = normalize.card_key(lot["title"])
        stats = floor = supply = None

        if comps_mode and key in comp_cache:
            same_card = normalize.comp_filter(lot["title"], comp_cache[key])
            stats = scoring.summarize(same_card, lot["grade_class"])
            if stats:
                archive.record_comp_stats(con, run_at, key, lot["grade_class"], stats)

        if access["browse"]:
            active = floor_cache.get(key) or []
            same = [a["price"] for a in active
                    if grading.classify_grade(a["title"]) == lot["grade_class"]]
            floor = min(same) if same else None
            supply = len(same) if active else None

        s = scoring.score_lot(lot, stats, floor, supply,
                              target_margin=target_margin, ad_rate=ad_rate,
                              store=store,
                              fc_stats=fc_comps_by_key.get((key, lot["grade_class"])),
                              fc_ratio=fc_ratio)
        no_comp_attempt = (not comps_mode) or key not in comp_cache
        if no_comp_attempt and s["verdict"] in ("NO_COMPS", "REJECT"):
            # We didn't look for comps — that's missing data, not a dead card.
            s["verdict"] = "NO_DATA"
            s["reason"] = ("comp quota exhausted before this lot" if quota_gone
                           else "no sold-comp source configured")
            s["floor"] = floor
            s["active_supply"] = supply
            # Floor-based signal only (asking price, NOT market value): what
            # you'd net listing 3% under the cheapest competing BIN, vs cost.
            if floor and s.get("current_total"):
                net_uc = fees.ebay_net_proceeds(floor * 0.97)
                all_in = fees.fc_total_cost(s["current_total"])
                s["net_if_undercut_floor"] = round(net_uc, 2)
                s["floor_gap_pct"] = round((net_uc / all_in - 1) * 100, 1)
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
    ap.add_argument("--margin", type=float, default=0.20, help="target net margin")
    ap.add_argument("--ad-rate", type=float, default=0.0, help="promoted listings rate")
    ap.add_argument("--store", action="store_true", help="eBay Basic Store fee schedule")
    ap.add_argument("--max-year", type=int, default=2009)
    ap.add_argument("--fc-history-cap", type=int, default=300,
                    help="max closed FC lots to fetch per run for FC comps (0 disables)")
    args = ap.parse_args(argv)

    if args.check_access:
        check_access()
        return 0

    scan(max_lots=args.max_lots, db_path=args.db, out_dir=args.out,
         target_margin=args.margin, ad_rate=args.ad_rate, store=args.store,
         skip_ebay=args.skip_ebay, max_year=args.max_year,
         fc_history_cap=args.fc_history_cap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
