"""Weekly report generation: one phone-readable HTML page + a CSV.

The operator reads this on his phone and places bids from it. Everything
that failed the velocity gate is already removed from the main table (a
collapsed section at the bottom shows what was filtered and why).
"""

import csv
import html
import io
from datetime import datetime, timezone, timedelta

PT = timezone(timedelta(hours=-7))  # Pacific Daylight Time (auction closes stated in PT)

CSV_COLUMNS = [
    "verdict", "title", "lot_string", "grade_class", "language", "product_line",
    "current_total", "max_bid_total", "max_bid_hammer", "headroom",
    "comp", "n_sales_90d", "last_sold", "confidence",
    "fc_comp", "fc_n_sales", "fc_last_sold", "win_likely",
    "floor", "active_supply", "bids", "auction_ends_at", "url", "reason",
]


def to_csv(scored_lots):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for s in scored_lots:
        w.writerow(s)
    return buf.getvalue()


def _fmt_money(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def _fmt_close(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PT)
        return dt.strftime("%a %b %d, %I:%M %p PT")
    except ValueError:
        return iso


_VERDICT_COLORS = {"BID": "#0a7d33", "WATCH": "#b57e00", "PASS": "#777",
                   "REJECT": "#a33", "NO_COMPS": "#a33", "NO_DATA": "#456"}


def _row(s):
    color = _VERDICT_COLORS.get(s.get("verdict"), "#555")
    conf = s.get("confidence")
    return f"""<tr>
<td><span class="v" style="background:{color}">{html.escape(s.get('verdict',''))}</span></td>
<td><a href="{html.escape(s.get('url',''))}">{html.escape(s.get('title',''))}</a>
  <div class="sub">{html.escape(s.get('lot_string') or '')} · {html.escape(s.get('grade_class',''))}
  · {html.escape(s.get('language',''))} · bids: {s.get('bids') if s.get('bids') is not None else '—'}</div></td>
<td class="num">{_fmt_money(s.get('current_total'))}</td>
<td class="num max">{_fmt_money(s.get('max_bid_total'))}
  <div class="sub">hammer {_fmt_money(s.get('max_bid_hammer'))}</div></td>
<td class="num">{_fmt_money(s.get('comp'))}
  <div class="sub">{s.get('n_sales_90d', 0)} sold/90d{(' · conf ' + format(conf, '.2f')) if conf is not None else ''}</div>
  {f'<div class="sub">FC clears {_fmt_money(s.get("fc_comp"))} ({s.get("fc_n_sales")}×){"" if s.get("win_likely", True) else " ⚠ above max"}</div>' if s.get('fc_comp') else ''}</td>
<td class="num">{_fmt_money(s.get('floor'))}
  <div class="sub">{s.get('active_supply') if s.get('active_supply') is not None else '—'} listed{
    (' · undercut nets ' + ('+' if s['floor_gap_pct'] >= 0 else '') + format(s['floor_gap_pct'], '.0f') + '%')
    if s.get('floor_gap_pct') is not None else ''}</div></td>
<td>{_fmt_close(s.get('auction_ends_at'))}</td>
</tr>"""


def _reject_row(s):
    return (f"<tr><td>{html.escape(s.get('title',''))}</td>"
            f"<td>{_fmt_money(s.get('current_total'))}</td>"
            f"<td>{html.escape(s.get('reason',''))}</td></tr>")


def to_html(scored_lots, auction_name="", comps_source="", generated_at=None,
            notes=()):
    generated_at = generated_at or datetime.now(timezone.utc)
    # NO_DATA (no comp source configured) stays visible in the main table:
    # the FC lot list + bid counts is still the whole deliverable in that mode.
    actionable = [s for s in scored_lots
                  if s.get("verdict") in ("BID", "WATCH", "NO_DATA")]
    passed = [s for s in scored_lots if s.get("verdict") == "PASS"]
    rejected = [s for s in scored_lots if s.get("verdict") in ("REJECT", "NO_COMPS")]

    note_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    rows = "".join(_row(s) for s in actionable) or \
        '<tr><td colspan="7">Nothing actionable this week.</td></tr>'
    pass_rows = "".join(_row(s) for s in passed)
    rej_rows = "".join(_reject_row(s) for s in rejected)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC → eBay scan · {html.escape(auction_name or '')}</title>
<style>
body{{font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:12px;background:#fafafa;color:#222}}
h1{{font-size:20px;margin:4px 0}} h2{{font-size:16px;margin:20px 0 6px}}
.meta{{color:#666;font-size:13px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:14px}}
th,td{{padding:7px 8px;border-bottom:1px solid #e5e5e5;text-align:left;vertical-align:top}}
th{{background:#f0f0f0;font-size:12px;text-transform:uppercase;letter-spacing:.03em}}
.num{{text-align:right;white-space:nowrap}} .max{{font-weight:700}}
.sub{{color:#888;font-size:12px;font-weight:400}}
.v{{color:#fff;border-radius:4px;padding:2px 7px;font-size:12px;font-weight:700}}
a{{color:#0b62c4;text-decoration:none}} a:hover{{text-decoration:underline}}
details{{margin-top:14px}} summary{{cursor:pointer;color:#555}}
.wrap{{overflow-x:auto}}
ul.notes{{color:#8a6d00;background:#fff8e0;border:1px solid #eedc9a;padding:10px 26px;border-radius:6px;font-size:13px}}
</style></head><body>
<h1>Fanatics → eBay weekly scan</h1>
<div class="meta">{html.escape(auction_name or 'Weekly auction')} ·
generated {generated_at.strftime('%Y-%m-%d %H:%M UTC')} ·
comps: {html.escape(comps_source or 'unavailable')} ·
{len(actionable)} actionable / {len(scored_lots)} scanned</div>
{f'<ul class="notes">{note_html}</ul>' if note_html else ''}
<div class="wrap"><table>
<tr><th></th><th>Card</th><th>Now (w/ BP)</th><th>Max bid (w/ BP)</th>
<th>eBay comp</th><th>Floor</th><th>Closes</th></tr>
{rows}
</table></div>
<details><summary>Priced past max bid ({len(passed)})</summary>
<div class="wrap"><table>
<tr><th></th><th>Card</th><th>Now (w/ BP)</th><th>Max bid (w/ BP)</th>
<th>eBay comp</th><th>Floor</th><th>Closes</th></tr>{pass_rows}</table></div></details>
<details><summary>Filtered out — illiquid / no comps ({len(rejected)})</summary>
<div class="wrap"><table><tr><th>Card</th><th>Now</th><th>Why filtered</th></tr>
{rej_rows}</table></div></details>
<p class="meta">Max bid = highest displayed price (incl. 20% premium) that still
nets the 20% target margin after the 3% vault withdrawal fee ($3 flat under
$50) and all eBay fees, selling at the comp. "hammer" is the number to
actually type in the bid box. No sales tax (vault purchase). Velocity gate:
anything with 0–2 sales in 90 days is filtered out automatically.</p>
</body></html>"""
