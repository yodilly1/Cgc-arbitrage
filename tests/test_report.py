from scanner import report


def _scored(verdict="BID"):
    return {
        "verdict": verdict, "title": "1999 Pokemon Jungle Snorlax #11 CGC 10 Gem Mint",
        "url": "https://www.fanaticscollect.com/weekly/x/y", "lot_string": "WA238 Lot: 1",
        "grade_class": "CGC10_GEM", "language": "EN", "product_line": "TCG",
        "current_total": 120.0, "max_bid_total": 340.0, "max_bid_hammer": 283.33,
        "headroom": 220.0, "comp": 500.0, "n_sales_90d": 8, "last_sold": "2026-08-01",
        "confidence": 0.82, "floor": 550.0, "active_supply": 3, "bids": 5,
        "auction_ends_at": "2026-08-10T02:00:00Z", "reason": "liquid",
    }


def test_csv_roundtrip():
    csv_text = report.to_csv([_scored()])
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("verdict,title")
    assert "Snorlax" in lines[1]


def test_html_sections():
    doc = report.to_html([_scored("BID"), _scored("PASS"), _scored("REJECT")],
                         auction_name="WA238", comps_source="test",
                         notes=["heads up"])
    assert "WA238" in doc
    assert "Snorlax" in doc
    assert "heads up" in doc
    assert "1 actionable / 3 scanned" in doc
    # rejected lots live in the collapsed section, not the main table
    assert "Filtered out" in doc


def test_html_escapes():
    s = _scored()
    s["title"] = 'Evil <script>alert(1)</script> & Co'
    doc = report.to_html([s])
    assert "<script>alert" not in doc
    assert "&lt;script&gt;" in doc


def test_no_data_rows_stay_visible():
    s = _scored("NO_DATA")
    s["comp"] = None
    doc = report.to_html([s])
    assert "NO_DATA" in doc and "Snorlax" in doc
    assert "1 actionable / 1 scanned" in doc
