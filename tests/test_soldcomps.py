import json
from unittest import mock

from scanner import soldcomps, scoring, archive


def _resp(status=200, items=None):
    r = mock.Mock()
    r.status_code = status
    r.text = ""
    r.json.return_value = {"items": items or []}
    return r


def _item(**kw):
    d = {"itemId": "1", "title": "1999 Pokemon Jungle Snorlax #11 CGC 10 Gem Mint",
         "soldPrice": "100.00", "soldCurrency": "USD", "endedAt": "2026-08-01",
         "condition": "Used", "bidCount": 5, "epid": "e1", "bestOfferAccepted": False}
    d.update(kw)
    return d


def test_maps_response_to_sale_dicts():
    c = soldcomps.SoldCompsClient(api_key="sc_test")
    with mock.patch("scanner.soldcomps.requests.get",
                    return_value=_resp(items=[_item()])):
        sales, err = c.sold("snorlax CGC 10")
    assert err is None
    s = sales[0]
    assert s["price"] == 100.0
    assert s["sold_date"] == "2026-08-01"
    assert s["best_offer"] is False


def test_quota_exhausted_raises():
    c = soldcomps.SoldCompsClient(api_key="sc_test")
    r = _resp(403)
    r.text = "Monthly quota exceeded"
    with mock.patch("scanner.soldcomps.requests.get", return_value=r):
        try:
            c.sold("x")
            assert False, "expected QuotaExhausted"
        except soldcomps.QuotaExhausted:
            pass


def test_revoked_key_is_auth_error_not_quota():
    # M7: a 401/403 without a quota message must NOT be mislabeled as quota
    c = soldcomps.SoldCompsClient(api_key="sc_bad")
    r = _resp(401)
    r.text = "Invalid API key"
    with mock.patch("scanner.soldcomps.requests.get", return_value=r):
        sales, err = c.sold("x")
    assert sales is None and "AUTH" in err


def test_old_sales_dropped():
    c = soldcomps.SoldCompsClient(api_key="sc_test")
    with mock.patch("scanner.soldcomps.requests.get",
                    return_value=_resp(items=[_item(endedAt="2025-01-01")])):
        sales, _ = c.sold("x", days=90)
    assert sales == []


def test_best_offer_counts_velocity_not_price():
    sales = [
        {"title": "Snorlax CGC 10 Gem Mint", "price": 100.0,
         "sold_date": "2026-08-01", "best_offer": False},
        {"title": "Snorlax CGC 10 Gem Mint", "price": 100.0,
         "sold_date": "2026-07-25", "best_offer": False},
        # inflated displayed price on an accepted offer:
        {"title": "Snorlax CGC 10 Gem Mint", "price": 900.0,
         "sold_date": "2026-07-20", "best_offer": True},
    ]
    st = scoring.summarize(sales, "CGC10_GEM")
    assert st["n_sales_90d"] == 3          # it DID sell — velocity counts it
    assert st["median"] == 100.0           # but its price is excluded
    assert st["last_3_avg"] == 100.0


def test_cached_sales_ttl(tmp_path):
    db = str(tmp_path / "t.sqlite")
    con = archive.connect(db)
    sale = {"item_id": "i1", "title": "Snorlax CGC 10 Gem Mint", "price": 50.0,
            "currency": "USD", "sold_date": "2026-08-01", "condition": "",
            "qty_sold": 1, "bid_count": None, "epid": "", "best_offer": True}
    archive.archive_sales(con, "q1", [sale], source="soldcomps")
    got = archive.cached_sales(con, "q1")
    assert got and got[0]["price"] == 50.0 and got[0]["best_offer"] is True
    assert archive.cached_sales(con, "q1", max_age_hours=0) is None
    assert archive.cached_sales(con, "never-fetched") is None
    # zero-result fetches are cached too (don't re-spend quota on dead queries)
    archive.archive_sales(con, "q2", [], source="soldcomps")
    assert archive.cached_sales(con, "q2") == []


def test_cached_lot_active_ttl(tmp_path):
    db = str(tmp_path / "t2.sqlite")
    con = archive.connect(db)
    lot = {"uuid": "u9", "url": "http://x", "title": "T CGC 10 Gem Mint",
           "listing_type": "WEEKLY", "grade_class": "CGC10_GEM", "language": "EN",
           "product_line": "TCG", "year": 1999, "hammer": 10.0,
           "price_incl_bp": 12.0, "bids": 2, "lot_string": "WA1 Lot: 1",
           "auction_name": "WA1", "auction_ends_at": "2026-08-10T02:00:00Z",
           "auction_status": "LIVE", "is_closed": False, "sold_date": None}
    archive.archive_lot(con, lot)
    con.commit()
    got = archive.cached_lot(con, "u9")
    assert got and got["price_incl_bp"] == 12.0 and got["is_closed"] is False
    assert archive.cached_lot(con, "u9", max_age_hours=0) is None
    lot["is_closed"] = True
    archive.archive_lot(con, lot)
    con.commit()
    assert archive.cached_lot(con, "u9", max_age_hours=0) is not None
