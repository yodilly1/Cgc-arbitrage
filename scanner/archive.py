"""SQLite archive.

eBay only exposes 90 days of sold data — anyone with deeper history built
their own archive. Every raw sale row we ever see is stored from day one;
week 52 of running this gives 15 months of comps nobody can sell you.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone

from . import grading

DB_PATH = os.environ.get("SPREAD_DB", "data/archive.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ebay_sales(
    item_id TEXT PRIMARY KEY,
    query TEXT, title TEXT, grade_class TEXT,
    price REAL, currency TEXT, sold_date TEXT, condition TEXT,
    qty_sold INTEGER, bid_count INTEGER, epid TEXT,
    captured_at TEXT);
CREATE TABLE IF NOT EXISTS fc_lots(
    uuid TEXT PRIMARY KEY,
    url TEXT, title TEXT, listing_type TEXT, grade_class TEXT,
    language TEXT, product_line TEXT, year INTEGER,
    hammer REAL, price_incl_bp REAL, bids INTEGER,
    lot_string TEXT, auction_name TEXT, auction_ends_at TEXT,
    auction_status TEXT, is_closed INTEGER, sold_date TEXT,
    captured_at TEXT);
CREATE TABLE IF NOT EXISTS scans(
    run_at TEXT PRIMARY KEY,
    auction_name TEXT, lots_seen INTEGER, lots_scored INTEGER,
    comps_source TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS comp_stats(
    run_at TEXT, card_key TEXT, grade_class TEXT, stats_json TEXT,
    PRIMARY KEY (run_at, card_key, grade_class));
CREATE TABLE IF NOT EXISTS sold_fetches(
    query TEXT PRIMARY KEY, source TEXT, fetched_at TEXT, n_results INTEGER);
"""


def _migrate(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(ebay_sales)")]
    if "best_offer" not in cols:
        con.execute("ALTER TABLE ebay_sales ADD COLUMN best_offer INTEGER DEFAULT 0")


def connect(path=None):
    path = path or DB_PATH
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    _migrate(con)
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def archive_sales(con, query, sales, source="insights"):
    now = _now()
    for s in sales:
        con.execute(
            "INSERT OR IGNORE INTO ebay_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["item_id"], query, s["title"], grading.classify_grade(s["title"]),
             s["price"], s.get("currency", "USD"), s["sold_date"],
             s.get("condition", ""), s.get("qty_sold", 1), s.get("bid_count"),
             s.get("epid", ""), now, 1 if s.get("best_offer") else 0))
    con.execute("INSERT OR REPLACE INTO sold_fetches VALUES (?,?,?,?)",
                (query, source, now, len(sales)))
    con.commit()


def cached_sales(con, query, max_age_hours=132):
    """Return archived sales for a query fetched within the TTL, else None.

    Default TTL (5.5 days) means the Friday scan's paid API calls are reused
    by Sunday's scan, but next week refetches. Keeps trial/monthly quota from
    being spent twice on the same card in one auction cycle.
    """
    row = con.execute("SELECT fetched_at FROM sold_fetches WHERE query=?",
                      (query,)).fetchone()
    if not row:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[0])
    except ValueError:
        return None
    if age.total_seconds() > max_age_hours * 3600:
        return None
    out = []
    for r in con.execute(
            "SELECT item_id,title,price,currency,sold_date,condition,qty_sold,"
            "bid_count,epid,best_offer FROM ebay_sales WHERE query=?", (query,)):
        out.append({"item_id": r[0], "title": r[1], "price": r[2],
                    "currency": r[3], "sold_date": r[4], "condition": r[5],
                    "qty_sold": r[6], "bid_count": r[7], "epid": r[8],
                    "best_offer": bool(r[9])})
    return out


def archive_lot(con, lot):
    con.execute(
        "INSERT OR REPLACE INTO fc_lots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lot["uuid"], lot["url"], lot["title"], lot["listing_type"],
         lot["grade_class"], lot["language"], lot["product_line"], lot["year"],
         lot["hammer"], lot["price_incl_bp"], lot["bids"], lot["lot_string"],
         lot["auction_name"], lot["auction_ends_at"], lot["auction_status"],
         1 if lot["is_closed"] else 0, lot["sold_date"], _now()))


def record_scan(con, auction_name, lots_seen, lots_scored, comps_source, notes=""):
    con.execute("INSERT OR REPLACE INTO scans VALUES (?,?,?,?,?,?)",
                (_now(), auction_name, lots_seen, lots_scored, comps_source, notes))
    con.commit()


def record_comp_stats(con, run_at, card_key, grade_class, stats):
    con.execute("INSERT OR REPLACE INTO comp_stats VALUES (?,?,?,?)",
                (run_at, card_key, grade_class, json.dumps(stats)))


def cached_lot(con, uuid, max_age_hours=6):
    """Reuse an archived lot row: closed lots never change (no TTL); active
    lots are reusable within `max_age_hours` (bids move, but not enough to
    refetch twice in one evening)."""
    row = con.execute(
        "SELECT uuid,url,title,listing_type,grade_class,language,product_line,"
        "year,hammer,price_incl_bp,bids,lot_string,auction_name,auction_ends_at,"
        "auction_status,is_closed,sold_date,captured_at FROM fc_lots WHERE uuid=?",
        (uuid,)).fetchone()
    if not row:
        return None
    if not row[15]:                                   # active -> check freshness
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row[17])
        except (ValueError, TypeError):
            return None
        if age.total_seconds() > max_age_hours * 3600:
            return None
    row = row[:17]
    keys = ["uuid", "url", "title", "listing_type", "grade_class", "language",
            "product_line", "year", "hammer", "price_incl_bp", "bids",
            "lot_string", "auction_name", "auction_ends_at", "auction_status",
            "is_closed", "sold_date"]
    d = dict(zip(keys, row))
    d["is_closed"] = bool(d["is_closed"])
    d["is_auction"] = d["listing_type"] in ("WEEKLY", "PREMIER")
    d["is_pokemon"] = grading.is_pokemon(d["title"])
    return d
