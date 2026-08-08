# Fanatics Collect → eBay CGC 10 Arbitrage Scanner

Every week this repo scans the live **Fanatics Collect Weekly Auction** for
pre-2010 Pokémon cards graded **CGC 10**, prices each one against **eBay
sold data**, filters out everything that isn't actually selling, and
publishes a ranked bid sheet you can read on your phone.

For each lot it shows: **the maximum you should bid**, the eBay comp behind
it, the 90-day sold count, the cheapest competing active listing, and a
clear verdict — with illiquid cards (the dead-inventory trap) already
removed.

---

## For Lee — how to use it

1. **Read the sheet.** Every Friday and Sunday a fresh report appears:
   - as an email (once email is set up below), and
   - at the project's GitHub Pages link (once enabled below), and
   - in this repo at [`reports/latest.html`](reports/latest.html).
2. **Only look at the green BID rows.** They're sorted best-first.
3. **The "Max bid (w/ BP)" column is the highest displayed price you can
   pay** — including Fanatics' 20% buyer's premium — and still clear the
   **20% target margin** after the vault withdrawal fee (3% when you pull a
   card within 90 days; flat $3 under $50) and every eBay fee, selling at
   the comp. No sales tax since purchases stay in the vault. The small
   "hammer" number underneath is what to actually type into the bid box.
4. Anything with **0–2 eBay sales in the last 90 days is already filtered
   out**, no matter how cheap. That's the point of the system: those are the
   cards that sit in inventory for months.

**One number to remember:** you need roughly a **20% spread just to break
even** (eBay fees + shipping + the 3% withdrawal fee — more like 24% on
sub-$300 cards), and about **44% to net a 20% margin**. "Buying at 80% of
market" only yields ~3–5% — far too thin. The sheet's max bid already
accounts for all of this.

## One-time setup (5 minutes, all clicking)

Someone (Lee or a helper) does this once in the GitHub repo settings:

1. **eBay keys** *(enables real sold comps — the system's core)*
   - Get production keys at [developer.ebay.com](https://developer.ebay.com)
     (Application Keys → Production → App ID + Cert ID).
   - Repo → Settings → Secrets and variables → Actions → New repository secret:
     - `EBAY_CLIENT_ID` = the App ID
     - `EBAY_CLIENT_SECRET` = the Cert ID
   - Note: sold-price data needs eBay's **Marketplace Insights** API, which
     is limited-release — apply for it in the eBay developer portal. The
     first scan log (Actions tab) reports exactly which access you have.
2. **SoldComps key** *(enables sold comps + the liquidity filter)*
   - Add secret `SOLDCOMPS_API_KEY` = your `sc_…` key from sold-comps.com.
   - Used automatically whenever eBay Marketplace Insights is unavailable.
   - Note: SoldComps scrapes eBay, which eBay's terms prohibit — realistic
     risk is IP/account blocking, not legal exposure. Operator's choice.
3. **Email delivery** *(optional)*
   - Create a free [resend.com](https://resend.com) account, copy the API key.
   - Add secrets `RESEND_API_KEY` and `REPORT_EMAIL` (the address to send to).
4. **Hosted report page** *(optional)*
   - Repo → Settings → Pages → Source: **GitHub Actions**.
5. The scan runs automatically Friday and Sunday (from the default branch).
   To run it right now: **Actions → weekly-scan → Run workflow**.

No secrets at all? It still runs — you get the lot sheet with Fanatics
prices and bid counts, just without eBay comps or the liquidity filter.

---

## How it works (technical)

```
weekly-auction sitemaps (.xml.gz)        the verified unlock: full live lot list
        │ slug pre-filter: pokemon + cgc-10 + year ≤ 2009 (~700 of ~50k lots)
        ▼
lot pages (server-rendered)              structured prefetchedItemData JSON:
        │                                hammer price, auction close, lot #,
        │                                + page-title verification (slugs lie)
        ▼
eBay Marketplace Insights (sold, 90d)    aspect_filter Professional Grader/Grade
        │  → Pristine / Gem Mint / Unspecified kept STRICTLY separate
        │  → every raw sale archived to data/archive.sqlite forever
        ▼
VELOCITY GATE (the whole game)           0 sales/90d → REJECT, 1–2 → REJECT,
        │                                3–5 → WATCH, 6+ → BID-eligible
        ▼
eBay Browse (active floor + supply)      exit check: undercut floor, still profit?
        ▼
scoring                                  max_bid = net(comp)/(1+margin_required),
        │                                margin widened when confidence is low
        ▼
reports/latest.html + .csv + .json       committed, emailed, published to Pages
```

### Module map

| Path | What it does |
|---|---|
| `scanner/fees.py` | Verified fee math both sides. Anchor test: `net(1000) == 855.10` |
| `scanner/grading.py` | CGC 10 Pristine ≠ Gem Mint ≠ Unspecified; language & Bandai/Topps flags |
| `scanner/normalize.py` | FC title → eBay query (Rocket Gang→Team Rocket, Nintedo→Nintendo, `#006`→`#6`) |
| `scanner/fanatics.py` | Sitemap ingest + lot-page parser (flight JSON + JSON-LD fallback), polite rate-limiting |
| `scanner/ebay.py` | OAuth, Marketplace Insights (sold), Browse (floor), Taxonomy, access probe |
| `scanner/scoring.py` | Comp stats, confidence, velocity gate, max-bid math, verdicts |
| `scanner/archive.py` | SQLite archive — eBay shows 90 days only; our own history compounds weekly |
| `scanner/report.py` | Phone-readable HTML + CSV |
| `scanner/run_scan.py` | Orchestrator CLI |

### CLI

```bash
pip install -r requirements.txt
python -m scanner.run_scan --check-access        # which eBay APIs work?
python -m scanner.run_scan --max-lots 25         # smoke test
python -m scanner.run_scan                       # full scan
python -m pytest tests/ -q                       # 30 tests, incl. real-page fixtures
```

### Price semantics worth knowing (verified live)

- FC lot pages embed `currentBid.amountInCents` — always the **hammer**.
- JSON-LD `offers.price` is the **hammer on active** lots but **includes the
  20% premium on closed** lots. The parser handles both; don't "simplify" it.
- Report totals are always **premium-inclusive**; the hammer is shown as the
  number to type in the bid box.

### If Marketplace Insights is denied

The scanner automatically degrades to Browse-only (active floor, clearly
labeled "not market value"). Alternatives, in order of value:
1. **Terapeak** (free in eBay Seller Hub, 3-year history) — manual, use it to
   spot-check the sheet.
2. **SoldComps** (~$29/mo) or **Apify `ebay-sold-listings`** (~$2.50/1k) —
   swap in behind `scanner/ebay.py`; ingestion is interface-isolated. Note:
   scraped eBay data breaches eBay's ToS (account/IP-block risk, not a
   crime) — that tradeoff is the operator's call, not the tool's.

### Open items (from the research brief)

- **Backtest the 30 seed lots** (`docs/BRIEF.md` §7) against same-quarter
  eBay comps once sold-data access exists — nobody has measured the real
  spread yet; it's the most valuable single output of this system.
- Premier auction sitemap ingestion (currently Weekly only; Premier lots
  still parse fine if fed individually).
- Feed realized outcomes (actual hammer + actual eBay sale) back into
  scoring calibration.

Full research context — verified facts, data-quality traps, and claims that
are provably false — lives in [`docs/BRIEF.md`](docs/BRIEF.md). Read it
before changing anything.
