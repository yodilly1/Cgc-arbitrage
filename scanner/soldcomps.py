"""SoldComps (sold-comps.com) — commercial eBay sold-listings provider.

The fallback sold-data source when Marketplace Insights is denied (which it
is, for this account). One GET returns up to 240 real completed sales.

    GET https://api.sold-comps.com/v1/scrape
    Authorization: Bearer sc_...
    ?keyword=...&count=...&sortOrder=endedRecently&sold=true

Verified live (Aug 2026). Response items carry endedAt, soldPrice, bidCount,
epid, and bestOfferAccepted — the last one matters: eBay displays inflated
prices for accepted Best Offers (trap #5), so those rows are flagged and
excluded from price stats while still counting toward sales velocity.

ToS note: this is scraped eBay data. eBay's User Agreement prohibits it;
realistic risk is IP/account blocking, not litigation. The operator chose
this tradeoff knowingly.

Key comes from env var SOLDCOMPS_API_KEY. Never hard-code it.
"""

import os
import time
from datetime import datetime, timezone, timedelta

import requests

API_URL = "https://api.sold-comps.com/v1/scrape"


class QuotaExhausted(Exception):
    """Monthly quota used up (HTTP 403) — stop calling for this run."""


class SoldCompsClient:
    def __init__(self, api_key=None, count=120):
        self.api_key = api_key or os.environ.get("SOLDCOMPS_API_KEY")
        self.count = count
        self._last = 0.0

    @property
    def has_key(self):
        return bool(self.api_key)

    def sold(self, query, days=90, retries=3):
        """Sold comps for a keyword. Returns (sales, None) or (None, err).

        Raises QuotaExhausted on 403 so the caller can stop spending requests.
        Sale dicts match scanner.ebay.EbayClient.sold() plus a `best_offer`
        flag. Rows older than `days` are dropped client-side.
        """
        for attempt in range(retries):
            wait = 1.2 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = requests.get(
                    API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    params={"keyword": query, "count": str(self.count),
                            "sortOrder": "endedRecently", "sold": "true"},
                    timeout=90)
            except requests.RequestException as e:
                if attempt == retries - 1:
                    return None, f"request failed: {e}"
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 403:
                raise QuotaExhausted(r.text[:200])
            if r.status_code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt * 10)
                continue
            if r.status_code != 200:
                return None, f"{r.status_code} {r.text[:200]}"
            break
        else:
            return None, "retries exhausted"

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        out = []
        for it in r.json().get("items", []) or []:
            ended = it.get("endedAt") or ""
            if ended and ended < cutoff:
                continue
            try:
                price = float(it.get("soldPrice") or 0)
            except (TypeError, ValueError):
                price = 0.0
            out.append({
                "item_id": str(it.get("itemId", "")),
                "title": it.get("title", ""),
                "price": price,
                "currency": it.get("soldCurrency", "USD"),
                "sold_date": ended,
                "condition": it.get("condition", ""),
                "qty_sold": 1,
                "bid_count": it.get("bidCount"),
                "epid": it.get("epid") or "",
                "best_offer": bool(it.get("bestOfferAccepted")),
                "buying_format": it.get("buyingFormat") or "",
            })
        return out, None
