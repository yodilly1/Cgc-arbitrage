"""Fee math for both sides of the FC -> eBay spread.

All constants verified against primary sources (Aug 2026) — see docs/BRIEF.md §2.
Unit-test anchor: ebay_net_proceeds(1000) == 855.10 exactly.
"""

# Fanatics Collect (buy side)
FC_BUYERS_PREMIUM = 0.20        # flat, Weekly + Premier; lot pages display price W/ premium

# Vault economics (verified fanaticscollect.com/thevault, Aug 2026):
# purchases vault for free with NO sales tax; withdrawing ("fulfilling") to
# ship out costs 1% of value — 3% if removed within 90 days of purchase.
# Items under $50 pay a flat $3 instead. The quick-flip-to-eBay flow always
# hits the 3% tier. ("Free if sold in 30 days" applies to selling ON
# Fanatics only — irrelevant here.)
FC_WITHDRAWAL_RATE_QUICK = 0.03   # removed within 90 days (the normal case)
FC_WITHDRAWAL_RATE_HELD = 0.01    # held 90+ days before withdrawal
FC_WITHDRAWAL_FLAT_UNDER_50 = 3.00
FC_WITHDRAWAL_FLAT_THRESHOLD = 50.0

# eBay (sell side) — trading cards category
EBAY_FVF = 0.1325               # non-Store, portion up to $7,500
EBAY_FVF_ABOVE = 0.0235         # portion above threshold
EBAY_FVF_THRESHOLD = 7500.0
EBAY_STORE_FVF = 0.1235         # Basic Store, portion up to $2,500
EBAY_STORE_THRESHOLD = 2500.0
EBAY_PER_ORDER = 0.40
SHIP_COST = 12.00               # label + signature confirmation (required >= $750) + packaging


def ebay_net_proceeds(sale_price, ad_rate=0.0, store=False):
    """What actually lands in your pocket after eBay fees + shipping."""
    fvf_rate = EBAY_STORE_FVF if store else EBAY_FVF
    threshold = EBAY_STORE_THRESHOLD if store else EBAY_FVF_THRESHOLD

    if sale_price <= threshold:
        fvf = sale_price * fvf_rate
    else:
        fvf = threshold * fvf_rate + (sale_price - threshold) * EBAY_FVF_ABOVE

    fees = fvf + EBAY_PER_ORDER + (sale_price * ad_rate)
    return sale_price - fees - SHIP_COST


def fc_withdrawal_fee(price_incl_bp, quick=True):
    """Vault fulfillment fee to ship a purchased card out."""
    if price_incl_bp < FC_WITHDRAWAL_FLAT_THRESHOLD:
        return FC_WITHDRAWAL_FLAT_UNDER_50
    rate = FC_WITHDRAWAL_RATE_QUICK if quick else FC_WITHDRAWAL_RATE_HELD
    return price_incl_bp * rate


def fc_total_cost(price_incl_bp, quick=True):
    """All-in cost of an FC purchase: displayed price (incl. 20% premium)
    + vault withdrawal fee. No sales tax (vault purchase)."""
    return price_incl_bp + fc_withdrawal_fee(price_incl_bp, quick)


def breakeven_ebay_price(fc_all_in_cost, ad_rate=0.0, store=False):
    """Minimum eBay sale price to break even on an all-in FC cost."""
    lo, hi = 0.0, max(fc_all_in_cost * 3, 100.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if ebay_net_proceeds(mid, ad_rate, store) < fc_all_in_cost:
            lo = mid
        else:
            hi = mid
    return hi


def max_fc_total(target_ebay_price, margin=0.20, ad_rate=0.0, store=False, quick=True):
    """Max displayed FC price (incl. 20% premium) that still nets `margin`
    when selling at `target_ebay_price`, after the vault withdrawal fee."""
    budget = ebay_net_proceeds(target_ebay_price, ad_rate, store) / (1.0 + margin)
    rate = FC_WITHDRAWAL_RATE_QUICK if quick else FC_WITHDRAWAL_RATE_HELD
    price = budget / (1.0 + rate)
    if price < FC_WITHDRAWAL_FLAT_THRESHOLD:
        price = budget - FC_WITHDRAWAL_FLAT_UNDER_50
    return max(price, 0.0)


def hammer_from_total(total_incl_bp):
    """FC lot pages show price INCLUDING the 20% premium."""
    return total_incl_bp / (1.0 + FC_BUYERS_PREMIUM)


def total_from_hammer(hammer):
    return hammer * (1.0 + FC_BUYERS_PREMIUM)
