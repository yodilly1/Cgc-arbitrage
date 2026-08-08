"""eBay clients: Marketplace Insights (sold comps) + Browse (active floor).

Reality check (do not re-litigate — see docs/BRIEF.md §3 and §5):
  - Public sold-data APIs died in Oct 2020. Browse returns ACTIVE listings
    only; there is NO `itemStatus:{SOLD}` filter, whatever eBay's own AI
    chatbot claims.
  - Marketplace Insights is the sanctioned sold path but is Limited Release
    (most applicants denied) and capped at 90 days. `category_ids` is
    required; `q`/`epid`/`gtin` must accompany it.
  - Everything degrades gracefully: with no credentials the scanner still
    runs FC-only; with Browse-only it adds the active floor; with Insights
    it adds real sold comps + velocity.

Credentials come from env vars only: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.
"""

import os
import time
import base64
from datetime import datetime, timezone, timedelta

import requests

HOST = "https://api.ebay.com"          # sandbox is useless here: fake data
OAUTH_URL = f"{HOST}/identity/v1/oauth2/token"
INSIGHTS_URL = f"{HOST}/buy/marketplace_insights/v1_beta/item_sales/search"
BROWSE_URL = f"{HOST}/buy/browse/v1/item_summary/search"
TAXONOMY_URL = f"{HOST}/commerce/taxonomy/v1"

SCOPE_BASE = "https://api.ebay.com/oauth/api_scope"
SCOPE_INSIGHTS = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"

CCG_SINGLES_CATEGORY = "183454"        # CCG Individual Cards
MARKETPLACE = "EBAY_US"


class EbayClient:
    def __init__(self, client_id=None, client_secret=None, category=CCG_SINGLES_CATEGORY):
        self.client_id = client_id or os.environ.get("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET")
        self.category = category
        self._tokens = {}

    @property
    def has_credentials(self):
        return bool(self.client_id and self.client_secret)

    def _token(self, scope):
        cached = self._tokens.get(scope)
        if cached and cached[1] > time.time() + 60:
            return cached[0], None
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = requests.post(
            OAUTH_URL,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": scope},
            timeout=30)
        if r.status_code != 200:
            return None, f"{r.status_code} {r.text[:300]}"
        j = r.json()
        tok = j.get("access_token")
        self._tokens[scope] = (tok, time.time() + int(j.get("expires_in", 7200)))
        return tok, None

    def _headers(self, tok):
        return {"Authorization": f"Bearer {tok}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE}

    # ---------------------------------------------------------- access probe

    def check_access(self):
        """Which APIs can this account actually use? Returns a dict of findings."""
        out = {"credentials": self.has_credentials, "browse": False,
               "insights": False, "detail": {}}
        if not self.has_credentials:
            out["detail"]["credentials"] = "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set"
            return out
        tok, err = self._token(SCOPE_BASE)
        out["browse"] = bool(tok)
        if err:
            out["detail"]["browse"] = err
            return out
        tok2, err2 = self._token(SCOPE_INSIGHTS)
        if not tok2:
            out["detail"]["insights"] = err2
            return out
        r = requests.get(
            INSIGHTS_URL, headers=self._headers(tok2),
            params={"q": "pokemon CGC 10", "category_ids": self.category, "limit": "1"},
            timeout=30)
        out["insights"] = r.status_code == 200
        if r.status_code != 200:
            out["detail"]["insights"] = f"token OK but call failed: {r.status_code} {r.text[:300]}"
        else:
            out["detail"]["insights"] = f"live, {r.json().get('total', 0)} matches for probe query"
        return out

    # ------------------------------------------------------------ sold comps

    def sold(self, query, grader=None, grade=None, days=90, limit=200, epid=None):
        """Sold comps from Marketplace Insights (90-day hard cap).

        Uses structured aspect_filter (Professional Grader / Grade) when
        given — far more reliable than regexing titles — and falls back
        without it if the category rejects aspect filters.
        """
        tok, err = self._token(SCOPE_INSIGHTS)
        if not tok:
            return None, f"no Marketplace Insights access: {err[:200]}"

        cutoff = datetime.now(timezone.utc) - timedelta(days=min(days, 90))
        base = {
            "category_ids": self.category,
            "filter": f"lastSoldDate:[{cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')}..]",
        }
        if epid:
            base["epid"] = epid
        else:
            base["q"] = query
        if grader and grade:
            base["aspect_filter"] = f"Professional Grader:{{{grader}}},Grade:{{{grade}}}"

        out, offset = [], 0
        while offset < limit:
            p = dict(base, offset=str(offset), limit=str(min(200, limit - offset)))
            r = requests.get(INSIGHTS_URL, headers=self._headers(tok),
                             params=p, timeout=30)
            if r.status_code != 200 and "aspect_filter" in base and r.status_code in (400, 500):
                base.pop("aspect_filter")
                p.pop("aspect_filter")
                r = requests.get(INSIGHTS_URL, headers=self._headers(tok),
                                 params=p, timeout=30)
            if r.status_code != 200:
                return None, f"{r.status_code} {r.text[:200]}"

            items = r.json().get("itemSales", []) or []
            if not items:
                break
            for it in items:
                price = it.get("lastSoldPrice", {}) or {}
                out.append({
                    "item_id": it.get("itemId", ""),
                    "title": it.get("title", ""),
                    "price": float(price.get("value", 0) or 0),
                    "currency": price.get("currency", "USD"),
                    "sold_date": it.get("lastSoldDate", ""),
                    "condition": it.get("condition", ""),
                    "qty_sold": it.get("totalSoldQuantity", 1),
                    "bid_count": it.get("bidCount"),
                    "epid": it.get("epid", ""),
                })
            offset += len(items)
            if offset >= 10000:
                break
            time.sleep(0.25)
        return out, None

    # ---------------------------------------------------------- active floor

    def active_listings(self, query, limit=100):
        """Current fixed-price listings, cheapest first — the undercut target."""
        tok, err = self._token(SCOPE_BASE)
        if not tok:
            return None, err
        r = requests.get(
            BROWSE_URL, headers=self._headers(tok),
            params={"q": query, "category_ids": self.category,
                    "filter": "buyingOptions:{FIXED_PRICE}",
                    "sort": "price", "limit": str(min(limit, 200))},
            timeout=30)
        if r.status_code != 200:
            return None, f"{r.status_code} {r.text[:200]}"
        out = []
        for it in r.json().get("itemSummaries", []) or []:
            price = it.get("price", {}) or {}
            out.append({
                "item_id": it.get("itemId", ""),
                "title": it.get("title", ""),
                "price": float(price.get("value", 0) or 0),
                "url": it.get("itemWebUrl", ""),
            })
        return out, None

    # ------------------------------------------------------------- taxonomy

    def find_category(self, term):
        tok, err = self._token(SCOPE_BASE)
        if not tok:
            return None, err
        r = requests.get(
            f"{TAXONOMY_URL}/category_tree/0/get_category_suggestions",
            headers=self._headers(tok), params={"q": term}, timeout=30)
        if r.status_code != 200:
            return None, f"{r.status_code} {r.text[:200]}"
        out = []
        for s in r.json().get("categorySuggestions", []) or []:
            c = s.get("category", {}) or {}
            path = " > ".join(
                a.get("category", {}).get("categoryName", "")
                for a in reversed(s.get("categoryTreeNodeAncestors", []) or []))
            out.append({"id": c.get("categoryId"), "name": c.get("categoryName"),
                        "path": path})
        return out, None
