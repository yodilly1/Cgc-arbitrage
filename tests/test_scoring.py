from datetime import datetime, timezone, timedelta

from scanner import scoring


def _recent(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def _sales(prices_dates, tier="CGC 10 Gem Mint"):
    return [{"title": f"1999 Pokemon Jungle Snorlax #11 {tier}",
             "price": p, "sold_date": d} for p, d in prices_dates]


def _recent_sales(prices, tier="CGC 10 Gem Mint", start=5, step=10):
    """N sales, each `step` days apart, all within the 90-day window."""
    return _sales([(p, _recent(start + i * step)) for i, p in enumerate(prices)], tier)


def _lot(total=120.0, tier="CGC10_GEM"):
    return {
        "uuid": "u", "url": "http://x", "title": "1999 Pokemon Jungle Snorlax #11 CGC 10 Gem Mint",
        "lot_string": "WA238 Lot: 1", "grade_class": tier, "language": "EN",
        "product_line": "TCG", "year": 1999, "bids": 5,
        "price_incl_bp": total, "hammer": total / 1.2,
        "auction_ends_at": "2026-08-10T02:00:00Z",
    }


def test_velocity_tiers():
    assert scoring.velocity_tier(0) == scoring.DEAD
    assert scoring.velocity_tier(2) == scoring.ILLIQUID
    assert scoring.velocity_tier(5) == scoring.SLOW
    assert scoring.velocity_tier(6) == scoring.LIQUID


def test_zero_sales_is_a_hard_reject():
    # Velocity is a GATE: no comps in tier -> REJECT no matter the discount
    s = scoring.score_lot(_lot(total=1.0), None)
    assert s["verdict"] == "NO_COMPS"
    stats = scoring.summarize(_sales([]), "CGC10_GEM")
    assert stats is None


def test_pristine_and_gem_never_pooled():
    sales = _recent_sales([100] * 6, tier="CGC 10 Gem Mint") + \
            _recent_sales([5000] * 6, tier="CGC 10 Pristine")
    gem = scoring.summarize(sales, "CGC10_GEM")
    pris = scoring.summarize(sales, "CGC10_PRISTINE")
    assert gem["n_sales_90d"] == 6 and gem["median"] == 100
    assert pris["n_sales_90d"] == 6 and pris["median"] == 5000


def test_illiquid_rejected_even_when_cheap():
    sales = _recent_sales([1000, 990])
    stats = scoring.summarize(sales, "CGC10_GEM")
    s = scoring.score_lot(_lot(total=100.0), stats)   # 90% "discount"
    assert s["verdict"] == "REJECT"
    assert "illiquid" in s["reason"]


def test_stale_sales_do_not_count_toward_velocity():
    # C2: 6 sales all older than 90 days must NOT pass the velocity gate
    stale = _sales([(500, _recent(120 + i * 5)) for i in range(6)])
    assert scoring.summarize(stale, "CGC10_GEM") is None
    # mixed: only the 2 recent ones count -> ILLIQUID, not LIQUID
    mixed = _sales([(500, _recent(120)), (500, _recent(100))] +
                   [(500, _recent(10)), (500, _recent(20))])
    st = scoring.summarize(mixed, "CGC10_GEM")
    assert st["n_sales_90d"] == 2


def test_liquid_cheap_lot_is_bid():
    sales = _recent_sales([500] * 8)
    stats = scoring.summarize(sales, "CGC10_GEM")
    s = scoring.score_lot(_lot(total=120.0), stats)
    assert s["verdict"] == "BID"
    assert s["max_bid_total"] > 120.0
    assert s["max_bid_hammer"] < s["max_bid_total"]


def test_overpriced_lot_passes():
    sales = _recent_sales([500] * 8)
    stats = scoring.summarize(sales, "CGC10_GEM")
    s = scoring.score_lot(_lot(total=490.0), stats)
    assert s["verdict"] == "PASS"


def test_exit_wall_demotes_bid_to_watch():
    # C3: cheaper same-card supply below cost -> can't exit at comp -> WATCH
    sales = _recent_sales([500] * 8)
    stats = scoring.summarize(sales, "CGC10_GEM")
    # floor $130 with 5 competing listings; all-in cost ~$124 -> undercut nets < cost
    s = scoring.score_lot(_lot(total=120.0), stats, floor_price=130.0, active_supply=5)
    assert s["exit_ok"] is False
    assert s["verdict"] == "WATCH"
    # thin supply (1 listing) is treated as noise -> stays BID
    s2 = scoring.score_lot(_lot(total=120.0), stats, floor_price=130.0, active_supply=1)
    assert s2["verdict"] == "BID"


def test_low_confidence_widens_margin():
    tight = _recent_sales([500] * 8, start=3, step=1)
    wild = _recent_sales([200, 900, 150, 800, 300, 700], start=5, step=12)
    st_t = scoring.summarize(tight, "CGC10_GEM")
    st_w = scoring.summarize(wild, "CGC10_GEM")
    assert st_t["confidence"] > st_w["confidence"]
    s_t = scoring.score_lot(_lot(), st_t)
    s_w = scoring.score_lot(_lot(), st_w)
    assert s_w["required_margin_pct"] > s_t["required_margin_pct"]


def test_comp_value_is_conservative():
    # Best-Offer bias skews sold averages HIGH -> take the lower of last3/median
    stats = {"last_3_avg": 600.0, "median": 500.0}
    assert scoring.comp_value(stats) == 500.0


def test_fc_summarize_and_win_likely():
    closed = [
        {"price_incl_bp": 120.0, "sold_date": "Feb 28, 2026"},
        {"price_incl_bp": 100.0, "sold_date": "Jan 10, 2026"},
        {"price_incl_bp": 140.0, "sold_date": "Mar 05, 2026"},
    ]
    st = scoring.fc_summarize(closed)
    assert st["n_sales"] == 3
    assert st["median"] == 120.0
    assert st["last_sold"] == "2026-03-05"

    sales = _recent_sales([500] * 8)
    stats = scoring.summarize(sales, "CGC10_GEM")
    s = scoring.score_lot(_lot(total=120.0), stats, fc_stats=st)
    assert s["fc_comp"] == 120.0
    assert s["win_likely"] is True          # FC clears $120, max bid is higher

    exp = scoring.fc_summarize([{"price_incl_bp": 9000.0, "sold_date": "Mar 01, 2026"}])
    s2 = scoring.score_lot(_lot(total=120.0), stats, fc_stats=exp)
    assert s2["win_likely"] is False        # FC always clears above max bid


def test_fc_summarize_empty():
    assert scoring.fc_summarize([]) is None
    assert scoring.fc_summarize([{"price_incl_bp": None, "sold_date": None}]) is None


def test_fc_calibration_ratio():
    pairs = [(85, 100)] * 6
    assert scoring.fc_calibration_ratio(pairs) == 0.85
    assert scoring.fc_calibration_ratio([(85, 100)]) == scoring.DEFAULT_FC_RATIO
    # garbage ratios clamp instead of poisoning the model
    assert scoring.fc_calibration_ratio([(10, 100)] * 6) == 0.4
    assert scoring.fc_calibration_ratio([(300, 100)] * 6) == 1.2


def test_blended_value_translates_fc_through_ratio():
    ebay = {"last_3_avg": 100.0, "median": 100.0}
    fc = {"last_3_avg": 85.0, "median": 85.0}
    v, src = scoring.blended_value(ebay, fc, fc_ratio=0.85)
    # FC $85 at ratio 0.85 implies $100 on eBay -> blend stays $100
    assert v == 100.0 and src == "ebay+fc"
    v2, src2 = scoring.blended_value(ebay, None)
    assert v2 == 100.0 and src2 == "ebay"
    v3, src3 = scoring.blended_value(None, fc, fc_ratio=0.85)
    assert v3 == 100.0 and src3 == "fc_only"
    assert scoring.blended_value(None, None) == (None, None)


def test_fc_only_valuation_caps_at_watch():
    fc = scoring.fc_summarize([
        {"price_incl_bp": 500.0, "sold_date": "Jul 01, 2026"},
        {"price_incl_bp": 520.0, "sold_date": "Jun 01, 2026"},
        {"price_incl_bp": 480.0, "sold_date": "May 01, 2026"},
    ])
    s = scoring.score_lot(_lot(total=120.0), None, fc_stats=fc, fc_ratio=0.85)
    assert s["verdict"] == "WATCH"          # never BID without eBay velocity
    assert s["value_source"] == "fc_only"
    assert "unverified" in s["reason"]
    assert s["comp"] > 500                  # translated to eBay-equivalent
