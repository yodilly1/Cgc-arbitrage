"""Comp summarization, velocity gating, and lot scoring.

Non-negotiable rules (docs/BRIEF.md §6):
  - Velocity is a GATE, not a score term. Zero recent sales in the exact
    grade tier = DO NOT BID, regardless of apparent discount. This is the
    operator's dead-inventory filter — the whole point of the system.
  - Pristine and Gem Mint comps are never pooled.
  - Every comp carries a confidence score; low confidence widens the
    required safety margin instead of being discarded.
"""

from datetime import datetime, timezone, timedelta

from . import fees, grading

# Velocity tiers (distinct sales in the last 90 days, exact grade tier)
DEAD = "DEAD"          # 0 sales  -> reject outright
ILLIQUID = "ILLIQUID"  # 1-2      -> flag prominently, usually reject
SLOW = "SLOW"          # 3-5
LIQUID = "LIQUID"      # 6+

# Operator target: 20% net margin on a quick sale at market price.
TARGET_MARGIN = 0.20


def velocity_tier(n_sales):
    if n_sales <= 0:
        return DEAD
    if n_sales <= 2:
        return ILLIQUID
    if n_sales <= 5:
        return SLOW
    return LIQUID


def summarize(sales, tier, window_days=90):
    """Trimmed, recency-ordered stats for one grade tier only.

    Accepted-Best-Offer rows (trap #5: eBay displays the pre-offer asking
    price, biasing averages HIGH) count toward velocity but are excluded
    from price stats — unless they're all we have.

    CRITICAL: velocity counts only sales within `window_days`. Without this,
    a cache replay of months-old rows resurrects a dead card as LIQUID — the
    exact dead-inventory trap the gate exists to prevent.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    rows = [s for s in sales
            if grading.classify_grade(s["title"]) == tier and s["price"] > 0
            and (s.get("sold_date") or "")[:10] >= cutoff]
    if not rows:
        return None
    rows.sort(key=lambda r: r["sold_date"] or "", reverse=True)
    priced = [r for r in rows if not r.get("best_offer")] or rows
    prices = sorted(r["price"] for r in priced)
    if len(prices) >= 5:                       # trim one outlier each side
        prices = prices[1:-1]

    n = len(prices)
    median = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2
    mean = sum(prices) / n
    var = sum((p - mean) ** 2 for p in prices) / n
    recent = [r["price"] for r in priced[:3]]

    return {
        "grade_class": tier,
        "n_sales_90d": len(rows),
        "median": round(median, 2),
        "mean": round(mean, 2),
        "cv": round((var ** 0.5) / mean, 3) if mean else None,
        "low": min(prices),
        "high": max(prices),
        "last_3_avg": round(sum(recent) / len(recent), 2),
        "last_sold": rows[0]["sold_date"][:10] if rows[0]["sold_date"] else "",
        "confidence": confidence(len(rows), rows[0]["sold_date"],
                                 (var ** 0.5) / mean if mean else 1.0),
    }


def confidence(n, last_sold_iso, cv):
    """0..1: sample size, recency, and price dispersion."""
    n_score = min(n / 10.0, 1.0)
    rec_score = 0.3
    if last_sold_iso:
        try:
            last = datetime.fromisoformat(last_sold_iso.replace("Z", "+00:00"))
            if last.tzinfo is None:      # bare-date form (e.g. SoldComps "2026-08-01")
                last = last.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last).days
            rec_score = max(0.0, 1.0 - age / 90.0)
        except ValueError:
            pass
    disp_score = max(0.0, 1.0 - min(cv if cv is not None else 1.0, 1.0))
    return round(0.45 * n_score + 0.35 * rec_score + 0.20 * disp_score, 2)


def fc_summarize(closed_lots):
    """Stats over Fanatics' OWN sold totals (incl. 20% BP) for one card+tier.

    This is the acquisition-market comp: what the card actually clears for
    on FC. eBay remains the valuation basis (it's the exit market); the FC
    comp calibrates what winning will probably cost.
    """
    rows = []
    for l in closed_lots:
        total = l.get("price_incl_bp")
        if not total:
            continue
        d = ""
        if l.get("sold_date"):
            try:
                d = datetime.strptime(l["sold_date"], "%b %d, %Y").date().isoformat()
            except ValueError:
                pass
        rows.append((d, float(total)))
    if not rows:
        return None
    rows.sort(reverse=True)
    totals = sorted(t for _, t in rows)
    n = len(totals)
    median = totals[n // 2] if n % 2 else (totals[n // 2 - 1] + totals[n // 2]) / 2
    recent = [t for _, t in rows[:3]]
    return {
        "n_sales": n,
        "median": round(median, 2),
        "last_3_avg": round(sum(recent) / len(recent), 2),
        "last_sold": rows[0][0],
    }


def comp_value(stats):
    """Market value estimate: blend of recent sales and 90d median.

    last_3_avg reacts to a moving market; the median resists single-sale
    noise. Best-Offer bias (accepted offers hidden, averages skew HIGH)
    argues for the conservative side, so take the LOWER of the two.
    """
    return min(stats["last_3_avg"], stats["median"])


DEFAULT_FC_RATIO = 0.85     # FC clears below eBay on average; refined per run


def fc_calibration_ratio(pairs, default=DEFAULT_FC_RATIO):
    """Median FC/eBay value ratio across cards priced in BOTH markets.

    FC auctions systematically clear below eBay (that gap is the business),
    so FC solds can't be averaged into eBay value raw — they must be
    translated through this ratio first. Clamped: a ratio outside
    [0.4, 1.2] means bad pairs, not a real market structure.
    """
    ratios = sorted(fc / eb for fc, eb in pairs if eb and fc)
    if len(ratios) < 5:
        return default
    r = ratios[len(ratios) // 2]
    return round(min(max(r, 0.4), 1.2), 3)


def blended_value(ebay_stats, fc_stats, fc_ratio=DEFAULT_FC_RATIO):
    """Fair market value ON THE EXIT MARKET (eBay), from both marketplaces.

    eBay solds are direct evidence (weight 0.7). FC solds are translated to
    eBay-equivalent via the calibration ratio (weight 0.3). FC-only pricing
    is allowed but flagged — it proves value, not eBay sell-through.
    Returns (value, source) where source is 'ebay+fc' | 'ebay' | 'fc_only'.
    """
    ebay_v = comp_value(ebay_stats) if ebay_stats else None
    fc_v = min(fc_stats["last_3_avg"], fc_stats["median"]) if fc_stats else None
    fc_implied = fc_v / fc_ratio if fc_v else None
    if ebay_v and fc_implied:
        return round(0.7 * ebay_v + 0.3 * fc_implied, 2), "ebay+fc"
    if ebay_v:
        return round(ebay_v, 2), "ebay"
    if fc_implied:
        return round(fc_implied, 2), "fc_only"
    return None, None


def score_lot(lot, stats, floor_price=None, active_supply=None,
              target_margin=TARGET_MARGIN, ad_rate=0.0, store=False,
              fc_stats=None, fc_ratio=DEFAULT_FC_RATIO):
    """Score one live FC lot against its comps.

    Valuation: blended FMV from eBay solds + ratio-translated FC solds.
    Liquidity: eBay velocity only — FC sales prove value, not eBay
    sell-through, so FC-only pricing caps at WATCH.
    """
    out = {
        "uuid": lot["uuid"], "url": lot["url"], "title": lot["title"],
        "lot_string": lot["lot_string"], "grade_class": lot["grade_class"],
        "language": lot["language"], "product_line": lot["product_line"],
        "year": lot["year"], "bids": lot["bids"],
        "current_total": lot["price_incl_bp"], "current_hammer": lot["hammer"],
        "auction_ends_at": lot["auction_ends_at"],
    }

    if fc_stats:
        out.update(fc_comp=min(fc_stats["last_3_avg"], fc_stats["median"]),
                   fc_n_sales=fc_stats["n_sales"],
                   fc_last_sold=fc_stats["last_sold"])

    market, value_source = blended_value(stats, fc_stats, fc_ratio)
    if market is None:
        out.update(verdict="NO_COMPS", velocity=DEAD,
                   reason="no sold data on either marketplace for this exact grade tier")
        return out
    out["value_source"] = value_source

    if stats:
        n = stats["n_sales_90d"]
        vel = velocity_tier(n)
        conf = stats["confidence"]
        out.update(velocity=vel, n_sales_90d=n, comp=market,
                   ebay_comp=comp_value(stats),
                   comp_median=stats["median"], comp_last3=stats["last_3_avg"],
                   last_sold=stats["last_sold"], confidence=conf)
        if vel == DEAD:
            out.update(verdict="REJECT", reason="0 eBay sales in 90 days — dead inventory risk")
            return out
        if vel == ILLIQUID:
            out.update(verdict="REJECT", reason=f"only {n} eBay sale(s) in 90 days — illiquid")
            return out
    else:
        # FC-only valuation: eBay liquidity unproven, margin padded hard.
        vel = None
        conf = 0.35
        out.update(velocity="UNKNOWN", n_sales_90d=0, comp=market, confidence=conf)

    # Low confidence widens the required margin rather than dropping the comp.
    required_margin = target_margin + (1.0 - conf) * 0.15
    max_total = fees.max_fc_total(market, required_margin, ad_rate, store)
    max_hammer = fees.hammer_from_total(max_total)
    net_at_market = fees.ebay_net_proceeds(market, ad_rate, store)

    # Cost basis is ALL-IN: displayed price (incl. 20% BP) + vault
    # withdrawal fee (3% quick-flip tier; flat $3 under $50). No sales tax.
    cur_total = lot["price_incl_bp"] or 0.0
    all_in = fees.fc_total_cost(cur_total) if cur_total else 0.0
    headroom = max_total - cur_total
    margin_now = (net_at_market - all_in) / all_in if all_in else None

    out.update(
        required_margin_pct=round(required_margin * 100, 1),
        max_bid_total=round(max_total, 2),
        max_bid_hammer=round(max_hammer, 2),
        net_if_sold_at_comp=round(net_at_market, 2),
        all_in_cost=round(all_in, 2) if all_in else None,
        breakeven_ebay_price=round(fees.breakeven_ebay_price(all_in, ad_rate, store), 2) if all_in else None,
        headroom=round(headroom, 2),
        margin_at_current_pct=round(margin_now * 100, 1) if margin_now is not None else None,
    )

    if floor_price is not None:
        undercut = floor_price * 0.97           # exit strategy: list just under floor
        net_uc = fees.ebay_net_proceeds(undercut, ad_rate, store)
        out.update(
            floor=floor_price,
            active_supply=active_supply,
            net_if_undercut_floor=round(net_uc, 2),
            exit_ok=bool(all_in) and net_uc >= all_in * 1.10,
        )

    # Acquisition odds: if this card's typical FC clearing price is above
    # the max bid, winning at a profitable price is unlikely.
    if out.get("fc_comp"):
        out["win_likely"] = out["fc_comp"] <= max_total

    # Exit-wall check (C3): if several cheaper same-card listings already sit
    # below our cost, we can't exit at the comp — we'd have to join that
    # cheaper supply. Demote BID->WATCH. A thin floor (1-2 listings) is
    # treated as noise, not a wall.
    exit_wall = (out.get("exit_ok") is False
                 and (active_supply or 0) >= 3)

    n = out.get("n_sales_90d", 0)
    if headroom <= 0:
        out.update(verdict="PASS", reason="current price already above max bid")
    elif vel is None:
        out.update(verdict="WATCH",
                   reason="FC comps only — eBay sell-through unverified")
    elif exit_wall:
        out.update(verdict="WATCH",
                   reason=f"{active_supply} cheaper listings below cost — "
                          f"can't exit at comp; bid only well under max")
    elif vel == SLOW:
        out.update(verdict="WATCH", reason=f"{n} sales/90d — slow mover, bid only well under max")
    else:
        out.update(verdict="BID", reason=f"liquid ({n} sales/90d), headroom ${headroom:,.0f}")
    return out


def rank_key(scored):
    """Sort: BID first, then WATCH, then floor-only rows; within a verdict by
    headroom x confidence (comp mode) or floor gap (floor-only mode)."""
    order = {"BID": 0, "WATCH": 1, "NO_DATA": 2, "PASS": 3, "REJECT": 4, "NO_COMPS": 5}
    signal = (scored.get("headroom") or 0) * (scored.get("confidence") or 0) \
        + (scored.get("floor_gap_pct") or 0)
    return (order.get(scored.get("verdict"), 6), -signal)
