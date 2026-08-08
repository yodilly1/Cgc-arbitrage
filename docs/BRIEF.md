# Build Brief: Fanatics Collect → eBay Arbitrage Scanner

**Paste this whole file as your opening prompt. It contains ~15 hours of verified research — including several things that are counterintuitive, plus at least three claims from "authoritative" sources that are provably FALSE. Read the VERIFIED FACTS and DO NOT REPEAT section before writing any code; they will save you days.**

---

## 1. Who this is for and what to build

**The operator (Lee) is non-technical.** He will not run scripts, manage environment variables, or operate a terminal. Any solution that requires him to execute code has failed. The deliverable must run on a schedule, in the cloud, and push results to him (email, Slack, a hosted web page). He can click links and paste API keys into a hosted service's settings UI — that's the ceiling.

**The business:** He buys pre-2010 Pokémon cards (English AND Japanese) graded **CGC 10** on Fanatics Collect **auctions**, then resells on **eBay**. The arbitrage is structural and real: Fanatics has a comparatively small bidder pool while eBay is enormous, so the same card frequently clears lower on FC than it sells for on eBay. He has already made money doing this manually.

**His actual problem is NOT finding cheap cards.** He can already do that. His problem is that **some cards sit in inventory for months** because they aren't in demand. Hundreds of lots close every week and he can't tell which discounts are real opportunities versus traps that will never sell.

So the system's job, in priority order:
1. **Filter out illiquid cards before he bids** (this is the whole game)
2. Compute the true margin after all fees on both sides
3. Rank what's left and deliver it weekly

**Scope:** Pokémon, released 2009 or earlier, English + Japanese, CGC 10, Fanatics Collect **auction lots only** (Weekly + Premier). Explicitly NOT fixed-price/Buy Now listings.

**Out of scope — do not build this:** He explicitly rejected cracking CGC slabs and regrading to PSA. Don't propose it, don't model it. Buy CGC 10 → sell CGC 10.

---

## 2. VERIFIED FACTS — do not re-research these

Every figure below was confirmed against a primary source. Trust them.

### Fanatics Collect (buy side)
- **Buyer's premium is 20%, flat.** Same for Weekly and Premier, no price tiers. Source: help.fanaticscollect.com "How It Works". (An earlier draft of this analysis said 22% — that was wrong.)
- **Lot pages display the price ALREADY INCLUDING the premium**, labeled literally `W/ Buyer's Premium`. So `hammer = displayed_price / 1.20`. Verified across 30 lots — every one divided cleanly by 1.2 into a round hammer number.
- Buyer also pays **sales tax** and **shipping** (Fanatics calculates shipping off final value, not a flat per-card rate).
- A **2.9% payment fee** applies to card payments but is **avoidable via ACH** — assume ACH, exclude it.
- **There is NO public Fanatics API.** Scraping is the only option.

### eBay (sell side)
- **Final value fee: 13.25%** for trading cards, non-Store seller, on the portion up to **$7,500**; 2.35% above that. Plus **$0.40 per order**.
- **eBay Basic Store: 12.35%** up to $2,500, then 2.35%. Store costs ~$21.95/mo billed annually. Break-even ≈ 2.5 sales/month at $1,000. **Starter Store gets NO discount** in this category.
- **Payment processing is bundled into the FVF.** There is no separate processing fee. Do not double-count it.
- FVF is charged on the **total** — item price + shipping the buyer pays + sales tax. Budget 0.5–1.3pp of extra drag.
- **International fee: 1.65%** when shipping outside the US.
- **Promoted Listings: 2% minimum** ad rate, charged on the same total base. A "typical" competitive rate for cards is reportedly 4–8% but this is **UNVERIFIED** — eBay's trending-rate page is dead.
- **Shipping a graded slab: ~$12 all-in.** USPS Ground Advantage label ~$5.50–6.50, plus **$4.15 signature confirmation which eBay REQUIRES on orders ≥$750** for seller protection, plus packaging.
- **eBay Standard Envelope CANNOT be used for graded slabs** — explicitly excluded by policy.
- **eBay Vault no longer exists.** Sold to PSA, closed May 2024. `pages.ebay.com/vault/fees` is stale 2023 content — ignore it.

### The break-even identity (verified by implementation)
```
net_proceeds(sale) = sale − FVF(sale) − $0.40 − (sale × ad_rate) − $12 shipping
break_even_ebay_price ≈ 1.15 × FC_price_incl_BP     (non-Store, no promo)
                      ≈ 1.22 × FC_price_incl_BP     (with 5% promoted listings)
```
Selling at $1,000 nets **$855.10** exactly. Use this as your unit test.

**You need ~15% spread just to break even, and ~44% for a 25% net margin.** Buying at "80% of market" — the operator's original mental model — yields only ~5% gross. Tell him this plainly; his stated target is too thin.

---

## 3. DATA ACCESS — the hard-won map

### Fanatics Collect: SOLVED, use this
- **Individual lot pages ARE fully server-rendered.** Plain HTTP GET returns title, final sale price, bid count, lot number, sold date, grade, year, set. No JS needed. This works — 30 lots were successfully pulled this way.
- URL patterns: **`/weekly/{uuid}/{slug}`** = Weekly Auction, **`/premier/{uuid}/{slug}`** = Premier. **`/fixed/...`** = Buy Now — **EXCLUDE these**, filtering on `/weekly/` and `/premier/` is a reliable auction-only filter.
- **`robots.txt` explicitly permits `/weekly/`.** Disallowed paths are only `/login`, `/join`, `/i-am-not-a-robot`, `/share/`, `/email/verify/*`.
- **The auction listing/browse page is JavaScript-only** — a plain fetch returns navigation chrome and zero lots. Don't waste time on it.
- **★ THE KEY UNLOCK YOU MUST USE:** `https://www.fanaticscollect.com/sitemap.xml` is a sitemap index containing **`https://www.fanaticscollect.com/sitemap/weekly-auction-1.xml.gz`** — the **currently active** weekly auction lots. There are also `sales-history-weekly-auction-1..53.xml.gz` (historical) and `premier-auction-1.xml.gz`. These are gzipped XML; decompress and you get the full current lot list. **The previous agent could not decompress .gz and this was the single blocker that stopped full automation. You almost certainly can. Start here.**
- Alternative/backup: the Apify actor `jungle_synthesizer/fanaticscollect-weekly-auction-scraper` (~$30/mo, has a scheduler, needs a residential proxy for Cloudflare, low adoption/unrated — treat as unproven).

### eBay sold prices: THE REAL BLOCKER
- **eBay killed public sold-data access in October 2020** (Finding API `findCompletedItems`). The Finding and Shopping APIs were fully decommissioned in 2025.
- **Browse API returns ACTIVE listings ONLY.** Useful for the "cheapest current BIN" floor check, useless as market value.
- **Marketplace Insights API** is the sanctioned sold-data path: `GET /buy/marketplace_insights/v1_beta/item_sales/search`, returns `lastSoldPrice` + `lastSoldDate`. **BUT:** it is **Limited Release** (spec says so explicitly), most applicants are denied, history is capped at **90 days**, `category_ids` is **required** (1–4 ids), you must also pass one of `q`/`epid`/`gtin`, and approved partners are given a restricted category allow-list. Scope: `https://api.ebay.com/oauth/api_scope/buy.marketplace.insights`.
- **Terapeak** (Seller Hub → Research) is **free with any seller account** and goes back **3 YEARS** with sell-through rates. It has **no API and no CSV export** — deliberately manual. It is the best data available and the hardest to automate. **Use it to validate your pipeline's output.**
- **eBay's 90-day wall is universal.** Anyone selling "deep eBay sold history" is selling their own archive. **Therefore: archive every sale you see into your own database from day one.** Week 1 gives 90 days; week 52 gives 15 months nobody can sell you.
- Commercial scraper options if Insights is denied: **SoldComps** (sold-comps.com, REST API, ~$29/mo for 10k requests, returns sold price + date) or **Apify `caffein.dev/ebay-sold-listings`** ($2.50/1k results, 2.5K users / 341K runs / 4.0★ — the usage numbers suggest it genuinely works).
- **ToS reality:** eBay's User Agreement prohibits automated access and commercializing eBay data without permission. This is contract breach, not a crime (post-*hiQ*), and realistic risk is IP/account blocking rather than litigation — but *eBay v. Bidder's Edge* is real precedent. Using a vendor shifts operational risk, not legal risk. **Surface this tradeoff to the operator and let him decide.** Do not silently commit him to it.

### ★ The aspect_filter unlock
eBay graded-card listings carry **structured item aspects**. On Browse and Marketplace Insights you can filter:
```
aspect_filter=Professional Grader:{CGC},Grade:{10}
```
This is dramatically more reliable than regexing titles and sidesteps most of the naming chaos in §4. Fall back to title parsing only if a category rejects aspect filters.

---

## 4. DATA QUALITY TRAPS — these will silently corrupt your output

**★ TRAP #1 — CGC "Pristine 10" ≠ CGC "Gem Mint 10". This is the most dangerous one.**
They are different grades with different markets. A confirmed real example: 1998 Japanese Vending Series 1 Snorlax #143 sold as a **CGC 10 Pristine for $5,990** while the **Gem Mint 10** of the same card was listed at **$499.95**. That's a **12x gap between two things both labeled "CGC 10."** Fanatics lots are usually Gem Mint. If you regex `CGC 10` and average, every comp you produce is garbage. **Parse `Pristine` / `P10` and keep the series separate. Also watch for plain "CGC 10" with no tier stated — bucket it as UNSPECIFIED, don't guess.**

**TRAP #2 — Fanatics URL slugs disagree with page content.** Observed: a slug ending `...suicune-27-cgc-10-gem-mint` served a page titled "Neo Revelation 1st Edition Holo Misdreavus #11". Slugs appear to be recycled. **Always parse the page body; never trust the URL.**

**TRAP #3 — Japanese naming is chaos.** All confirmed in Fanatics' own catalog:
- Japanese Team Rocket is listed as **"Rocket Gang"** — eBay/PSA say "Team Rocket". A keyword join misses it entirely.
- `"CoroCoro"` vs `"Corocoro"`; `"VS"` vs `"Vs"` — case-sensitive matching fails.
- Word order is unstable: "CoroCoro Comic Promo" / "Promo CoroCoro Comic" / "Promo Corocoro Magazine" — no canonical string.
- **"Nintedo Error" is misspelled in Fanatics' own titles and slugs** (missing the n). Searching "Nintendo Error" returns nothing.
- The word **"Japanese" is sometimes omitted** from Japanese cards. Don't use it as the language flag.
- Card numbers inconsistently zero-padded: `#6` vs `#006`, `#94` vs `#094`. Normalize numerically.
- **Year attribution is unreliable** — Ancient Mew appears as both 1999 and 2000; Japanese Base Set as 1996 and 1997. A `year <= 2009` filter is safe, but **year cannot be a matching key**.
- Lot number formats vary: `WA187 Lot 8103`, `WA128 Lot: 6004` (colon), `6379F1` (sub-lot suffix), and Premier uses a bare `82F14` with no WA prefix — **a regex tuned to Weekly silently drops all Premier lots.**

**TRAP #4 — Bandai Carddass is not a TCG card.** It sits in the same Pokémon taxonomy with identical title structure but is a separate collectible category with a different buyer pool and much thinner comps. Flag and segregate it.

**TRAP #5 — eBay hides accepted Best Offer prices.** 130point surfaces them; eBay's own sold page does not. **Your scraped averages will be biased HIGH on Best-Offer-heavy cards.**

---

## 5. DO NOT REPEAT — claims that are provably FALSE

**❌ eBay's own Developer AI chatbot gave the operator this advice, and it is wrong:**
> *"Use the Browse API… `filter=itemStatus:{SOLD}` will return sold listings."*

Verified against eBay's official filter reference: **there is no `itemStatus` filter.** The 28 supported Browse filters are bidCount, buyingOptions, charityOnly, conditionIds, conditions, deliveryCountry, deliveryOptions, deliveryPostalCode, excludeSellers, excludeCategoryIds, itemEndDate, itemStartDate, itemLocationCountry, itemLocationRegion, maxDeliveryCost, paymentMethods, pickup*, price, priceCurrency, priorityListing, qualifiedPrograms, returnsAccepted, searchInDescription, sellerAccountTypes, sellers. **`itemEndDate` exists but means "scheduled to end" — future end dates on ACTIVE listings, the opposite of sold.** Browse has no `lastSoldPrice`/`lastSoldDate` fields at all. The bot appears to be blending Browse with Marketplace Insights and the dead `findCompletedItems`. **Do not trust that chatbot.**

**❌ PriceCharting is NOT a usable CGC source.** It only added CGC support in June 2024 and its coverage is negligible. Measured directly: Base Set Charizard's page had **174 sold rows with only 9 CGC mentions — all grades 7–8, zero CGC 10.** Neo Genesis Lugia: **3 CGC rows out of 51.** Its `condition-17-price` ("CGC 10") field is a thin estimate, and its API **explicitly has no historical/sold data at all** — current values only. A previous analysis wrongly concluded "no CGC 10 market exists" from this source; that conclusion was an artifact of PriceCharting's coverage gap, not a market fact. **Do not repeat that error — and do not assume the CGC 10 market is dead. The operator makes money in it.**

**❌ eBay sandbox keys are useless here.** Sandbox is populated with fake listings — no real cards, no real sold prices. It will return confident nonsense.

**❌ 22% buyer's premium** — it's 20%.

---

## 6. What to build

```
  WEEKLY (scheduled, cloud-hosted, no operator action)
  │
  ├─ 1. INGEST ── decompress /sitemap/weekly-auction-1.xml.gz
  │               → all live lot URLs → fetch each lot page (rate-limited)
  │               → parse: title, price incl. BP, bids, lot#, close date, grade
  │               → filter: /weekly|premier/, Pokémon, year ≤ 2009, CGC 10
  │
  ├─ 2. COMP ──── for each card, get eBay CGC 10 SOLD comps
  │               (Marketplace Insights if approved → else SoldComps/Apify)
  │               → SPLIT Pristine vs Gem Mint vs Unspecified
  │               → trimmed recency-weighted mean + confidence score
  │               → ARCHIVE every raw sale row to your own DB
  │
  ├─ 3. VELOCITY ─ ★ HARD GATE, not a score term ★
  │               count distinct sales in last 90d for the exact grade tier
  │               0 sales     → DO NOT BID (this is his dead-inventory filter)
  │               1-2 sales   → ILLIQUID, flag prominently
  │               3-5         → SLOW
  │               6+          → LIQUID
  │
  ├─ 4. FLOOR ──── cheapest ACTIVE CGC 10 BIN on eBay (Browse API)
  │               + how many competing listings exist (supply depth)
  │               his exit strategy is undercutting, so he must be able to
  │               list BELOW this floor and still profit
  │
  ├─ 5. SCORE ──── max_bid = f(comp, target_margin, all fees)
  │               rank by margin × velocity; drop anything failing gates
  │
  └─ 6. DELIVER ── ranked sheet emailed weekly + hosted web page
                   columns: card, lot#, current bid, MAX BID, comp,
                   90d sales count, active supply, floor, margin, confidence
```

**Non-negotiable design rules:**
- **Velocity is a gate, not a weight.** Zero recent sales = reject regardless of discount. This single rule addresses his stated #1 pain.
- **Never pool Pristine and Gem Mint comps.**
- **Every comp carries a confidence score** (n, recency, variance). Low confidence widens the safety margin rather than being discarded.
- **Archive all raw sales from day one** — the 90-day wall makes your own DB the only appreciating asset here.
- **Keep ingestion behind an interface** so the Fanatics source can be swapped without touching comp/scoring logic.
- **Feed realized results back**: post-auction, record actual hammer prices and (when he sells) actual eBay outcomes, to calibrate the model.
- Be polite when scraping: rate-limit, identify the client, cache aggressively.

---

## 7. Seed data — 30 REAL closed FC auction lots

Use these as test fixtures for your parser and for backtesting. All are real, all include the 20% premium, all verified from live lot pages.

**English (14):** Shadowless 1st Ed Charizard #4 **$33,600** (Premier 82F14, 18 bids, Mar 2024) · Skyridge Crystal Charizard #146 **$10,200** (WA133, 47 bids, Aug 2024) · Shadowless 1st Ed Blastoise #2 **$8,700** (WA128, 42 bids, Jul 2024) · Neo Genesis 1st Ed Lugia #9 **$4,440** (WA148, 55 bids, Nov 2024) · Skyridge Gyarados #H10 **$3,840** (WA171, 36 bids, Apr 2025 — *label ambiguous, may be Pristine*) · Gym Heroes 1st Ed Sabrina's Gengar #14 **$2,250** (WA180, 39 bids) · Neo Destiny 1st Ed Shining Raichu #111 **$1,860** (WA174, 23 bids) · Fossil 1st Ed Gengar #5 **$1,590** (WA91, 35 bids) · Expedition Dragonite #9 **$1,470** (WA180, 40 bids) · Jungle 1st Ed Snorlax #11 **$1,290** (WA145, 41 bids) · Gym Challenge 1st Ed Misty's Gyarados #13 **$720** (WA184, 17 bids) · Neo Revelation Entei #6 **$468** (WA165, 21 bids) · Fossil 1st Ed Ditto #3 **$312** (WA187, 18 bids) · Neo Destiny Dark Typhlosion #10 **$156** (WA175, 20 bids)

**Japanese (16):** Neo 4 Shining Charizard #6 **$7,500** (WA195, 26 bids, Oct 2025) · Bandai Carddass Prism Charizard #006 **$1,500** (WA174, 23 bids — *not TCG*) · Movie Promo "Nintedo" Error Ancient Mew **$630** (WA187, 22 bids) · CoroCoro Hama-Chan's Slowking **$540** (WA209, 17 bids, Jan 2026) · Red/Green Gift Set Articuno #144 **$516** (WA189, 20 bids) · Vending Series 1 Snorlax #143 **$444** (WA190, 26 bids) · CoroCoro Erika's Dratini #147 **$396** (WA191, 27 bids) · Jungle Snorlax #143 **$324** (WA179, 12 bids) · Rocket Gang Dark Hypno #97 **$156** (WA228, 12 bids, Jun 2026) · CD Promo Glossy Mewtwo #150 **$138** (WA136, 18 bids) · Vending Series 3 Pokemon Machine **$86.40** (WA140, 11 bids) · 3D Blue Poker Set Gengar **$76.80** (WA151, 15 bids) · Neo Premium File Typhlosion #157 **$57.60** (WA152, 8 bids) · Neo 4 Dark Ampharos #181 **$54** (8 bids) · CoroCoro Glossy Computer Error **$33.60** (WA181, 15 bids) · Neo Premium File 3 Raikou #243 **$31.20** (WA147, 7 bids)

Note the pattern: sub-$100 lots consistently draw 7–11 bids while $300+ lots draw 12–27. **Bid count is a usable proxy for demand** and it's free in the FC data — consider it as a secondary velocity signal, especially where eBay comps are thin.

---

## 8. Starting code

A working Python module is included in the repo (`fc_ebay_spread.py`). It already implements, tested:
- Correct fee math both sides (`ebay_net_proceeds(1000) == 855.10` — use as a unit test)
- `breakeven_ebay_price()`, `max_fc_bid()`, `hammer_from_total()`
- Grade classification splitting Pristine / Gem Mint / Unspecified
- Fanatics lot-page parser (regex-based, works on real pages)
- Marketplace Insights + Browse clients with OAuth, aspect_filter, `lastSoldDate` bounding, pagination
- Taxonomy API category resolution (`category_ids` is required by Insights)
- Velocity gating and SQLite archiving

**Treat it as a reference implementation, not gospel** — it was never run against live credentials. Verify every API call against real responses before trusting it.

---

## 9. Open questions to resolve early

1. **Does the operator have Marketplace Insights access?** Run the access check first — it determines the entire data architecture. If denied, go straight to SoldComps/Apify. Don't design around a maybe.
2. **Does the `weekly-auction-1.xml.gz` sitemap contain the *current* auction, and how fresh is it?** This is the linchpin of automation. Verify before building on it.
3. **How is CGC tier (Pristine vs Gem Mint) represented in Fanatics lot HTML?** Sometimes the title says PRISTINE while the body says only "CGC 10". Find the authoritative field.
4. **What is the real FC→eBay spread?** Backtest the 30 seed lots against eBay sold comps from the same quarter. This tells you whether the business is 15% or 60% — nobody has measured it yet. **This is the single most valuable thing you can produce.**
5. **Beware date-mismatched comps.** The seed lots span Oct 2023 – Jun 2026 and vintage Pokémon moved considerably in that window. Compare same-quarter or adjust.

---

## 10. Definition of done

A scheduled cloud job that each week emails Lee a ranked sheet of the current Fanatics auction's pre-2010 Pokémon CGC 10 lots, showing for each: **the maximum he should bid**, the eBay comp behind it, the **90-day sold count**, competing active supply, and a clear liquidity verdict — with everything that has no recent sales already removed.

He should be able to read it on his phone and place bids without opening a terminal, ever.
