"""Parser tests against REAL captured lot pages (fetched 2026-08-08)."""
import os

from scanner import fanatics

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name)) as fh:
        return fh.read()


def test_parse_active_lot():
    url = ("https://www.fanaticscollect.com/weekly/0d81d330-818a-11f1-b87c-0a58a9feac02/"
           "2007-pokemon-japanese-secret-of-the-lakes-exeggcute-cgc-10-gem-mint")
    lot = fanatics.parse_lot_html(_load("active_lot.html"), url)
    assert lot["title"] == "2007 Pokemon Japanese Secret Of The Lakes Exeggcute CGC 10 GEM MINT"
    assert lot["listing_type"] == "WEEKLY"
    assert lot["is_auction"] is True
    assert lot["is_closed"] is False
    assert lot["auction_status"] == "LIVE"
    assert lot["hammer"] == 11.0
    # ACTIVE lots: JSON-LD price is the raw current bid; total must add the 20%
    assert lot["price_incl_bp"] == round(11.0 * 1.2, 2)
    assert lot["grade_class"] == "CGC10_GEM"
    assert lot["language"] == "JA"
    assert lot["year"] == 2007
    assert lot["auction_ends_at"] == "2026-08-10T02:00:00Z"


def test_parse_closed_lot():
    url = ("https://www.fanaticscollect.com/weekly/2138f1d8-6f23-11ed-8144-0a58a9feac02/"
           "2020-pokemon-japanese-swsh-shiny-star-v-amazing-rare-yveltal-117-cgc-10")
    lot = fanatics.parse_lot_html(_load("closed_lot.html"), url)
    assert lot["is_closed"] is True
    assert lot["hammer"] == 110.0            # currentBid 11000 cents
    assert lot["price_incl_bp"] == 132       # displayed W/ Buyer's Premium
    assert abs(lot["price_incl_bp"] - lot["hammer"] * 1.2) < 0.01
    assert lot["bids"] == 12
    assert lot["lot_string"] == "WA6 Lot: 5774"
    assert lot["sold_date"] == "Feb 28, 2022"
    assert lot["grade_class"] == "CGC10_UNSPEC"   # no tier stated -> never guess


def test_slug_prefilter():
    urls = [
        "https://x/weekly/u1/1999-pokemon-base-set-charizard-4-cgc-10-gem-mint",
        "https://x/weekly/u2/2020-pokemon-swsh-pikachu-cgc-10-gem-mint",       # too new
        "https://x/weekly/u3/1999-pokemon-base-set-blastoise-2-psa-10",        # not CGC
        "https://x/weekly/u4/2003-topps-lebron-james-rookie-cgc-10",           # not pokemon
        "https://x/weekly/u5/1998-pokemon-vending-snorlax-143-cgc-10-pristine",
    ]
    out = fanatics.slug_candidates(urls)
    assert [u.split("/")[-2] for u in out] == ["u1", "u5"]
