"""
Scraper for Auchan Polska's online grocery shop (zakupy.auchan.pl/promotions).

BACKGROUND:
auchan.pl (the main marketing site) only links to PDF/image gazetka flyers,
same dead end as every other store's marketing site. But Auchan also runs a
full online grocery shop at zakupy.auchan.pl (reached via the "Zakupy
online" link in auchan.pl's header), and its /promotions page lists real,
current promotional items with genuine old/new prices — not just a curated
highlight list like the Blix.pl-sourced scrapers.

HOW THIS WORKS (confirmed live in a browser while building this):
zakupy.auchan.pl is a React app that server-renders its INITIAL page load,
embedding a `window.__INITIAL_STATE__ = {...}` JSON blob directly in a
<script> tag. That blob's `data.products.productEntities` map already
contains FULL product objects — name, original/current price, discount
description, category path, image URL, retailer product ID — for every
product tile actually rendered in that first server-rendered page (~50
items). This scraper does exactly one GET request (the page load itself)
and reads that embedded data. No further requests are made.

WHY THIS SHAPE, NOT THE FULLER ONE (READ THIS BEFORE "IMPROVING" IT):
An earlier version of this scraper also pulled the *full* ~300-item
product-id list out of `data.products.catalogue.data.productGroups[0]
.products` and batch-fetched full details for all of them via:
    PUT https://zakupy.auchan.pl/api/webproductpagews/v6/products
This is the same call the site's own JS makes as you scroll further down
the page. It worked when this was first built by hand in a browser, but:
  1. https://zakupy.auchan.pl/robots.txt disallows `Disallow: /api/`
     outright. Calling that endpoint programmatically — even with a
     legitimate-looking browser User-Agent — goes against the site's own
     stated crawling policy. /promotions itself and /products/<id> pages
     are NOT disallowed, which is why this version sticks to a plain GET
     of /promotions only.
  2. It's also blocked in practice: every batch call 403'd when run from
     GitHub Actions' IPs (AWS WAF bot detection), confirmed across a real
     workflow run. So it wasn't reliable even ignoring point 1.
Net effect: this scraper deliberately caps itself at whatever's already
embedded in the one allowed page load (~50 items) rather than chasing the
larger but disallowed/blocked list. That's a real trade — fewer items —
made on purpose for both the legal/compliance reason and the reliability
one. Do not "fix" this by re-adding the /api/ batch calls.

CSS-class-based scraping was ruled out: Auchan's product tiles are built
with styled-components, whose classes (e.g. "sc-mmemlz-0 fEEncK") are
hashed per build and not stable across deploys — see scrape_kaufland.py /
scrape_biedronka.py for how BEM-style stable classes were used there
instead; Auchan's markup doesn't offer that, hence this SSR-JSON approach.

IMPORTANT — READ BEFORE RELYING ON THIS DATA:
- This is a partial list: whatever's embedded in the /promotions page's
  first server-rendered load, confirmed to be ~50 items at the time this
  was built — not Auchan's full current promotions catalog (~300 items,
  per `totalProducts` in the same payload), and not the full store
  catalog. Same "list what one page/load gives you" scope every other
  scraper in this repo also settles for, just a smaller page here because
  the rest requires the disallowed API.
- zakupy.auchan.pl sits behind AWS WAF (a `mp_verify` bot-detection
  request fires on page load, and there's an `aws-waf-token` cookie).
  Simple GETs of ordinary pages worked fine every time this was tested; if
  this step starts failing in CI, bot-detection blocking the datacenter/CI
  IP — same risk noted in scrape_lidl.py — is the most likely reason. It's
  marked continue-on-error in the workflow for that reason.
- Product URLs use Auchan's real /products/<slug>/<retailerProductId>
  format. The slug is rebuilt from the product name with `slugify()`
  rather than read from an explicit field (none is present in the SSR
  data) — confirmed to exactly match Auchan's own slugs for every sampled
  product while building this, but treat it as best-effort; the numeric ID
  at the end should still resolve correctly even if a slug ever mismatches.

Usage:
    pip install requests --break-system-packages
    python scrape_auchan.py

Output:
    auchan_promocje.json — current promo items from zakupy.auchan.pl/promotions
"""

import json
import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

BASE_URL = "https://zakupy.auchan.pl"
PROMOTIONS_PATH = "/promotions"  # allowed by robots.txt — this is the only URL this scraper fetches


def slugify(name):
    """Best-effort rebuild of Auchan's product-URL slug from the product
    name — see the module docstring's URL caveat."""
    name = name.lower()
    replacements = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    }
    for pl, ascii_ in replacements.items():
        name = name.replace(pl, ascii_)
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return name


def fetch_initial_state():
    """GET the promotions page (the only request this scraper makes) and
    pull the embedded __INITIAL_STATE__ JSON out of its <script> tag."""
    resp = requests.get(BASE_URL + PROMOTIONS_PATH, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    marker = "window.__INITIAL_STATE__"
    start = html.find(marker)
    if start == -1:
        raise RuntimeError("__INITIAL_STATE__ not found in page — Auchan may have changed their SSR setup")

    # The JSON blob runs from the '=' after the marker up to the closing
    # ';</script>' of THIS specific script tag. Walk forward from the
    # assignment and track brace depth (ignoring braces inside JSON string
    # literals) to find the exact end, since naive ';</script>' search can
    # match inside escaped JSON-within-JSON content elsewhere in the blob.
    eq_idx = html.find("=", start)
    json_start = eq_idx + 1
    i = json_start
    depth = 0
    in_string = False
    escape = False
    started = False
    while i < len(html):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    i += 1
                    break
        i += 1
    json_str = html[json_start:i].strip()
    return json.loads(json_str)


def parse_entity(p):
    """Parse one entry from data.products.productEntities — the SSR-embedded
    shape, distinct from the (unused) API response shape:
    price is {original: {amount}, current: {amount}}, and the discount
    blurb lives at offer.description rather than promotions[0].description."""
    name = p.get("name")
    if not name:
        return None

    price = p.get("price") or {}
    old_price = (price.get("original") or {}).get("amount")
    new_price = (price.get("current") or {}).get("amount")
    if new_price is None:
        return None
    try:
        old_price = float(old_price) if old_price is not None else None
        new_price = float(new_price)
    except (TypeError, ValueError):
        return None
    if old_price is not None and old_price <= new_price:
        old_price = None  # not actually discounted — don't show a fake strikethrough

    discount_pct = (
        round((1 - new_price / old_price) * 100) if old_price and new_price else None
    )

    # Only surface the raw promo blurb as a note when we couldn't cleanly
    # derive a discount % from original/current price (e.g. bundle deals
    # like "2+1 za grosz") — otherwise it'd just duplicate the discount badge.
    offer = p.get("offer") or {}
    note = offer.get("description") if offer.get("description") and discount_pct is None else None

    category_path = p.get("categoryPath") or []
    category = category_path[1] if len(category_path) > 1 else (
        category_path[0] if category_path else "Inne"
    )

    image = (p.get("image") or {}).get("src")

    retailer_id = p.get("retailerProductId")
    url = (
        f"{BASE_URL}/products/{slugify(name)}/{retailer_id}"
        if retailer_id else BASE_URL + PROMOTIONS_PATH
    )

    return {
        "store": "Auchan",
        "category": category,
        "name": name,
        "oldPrice": old_price,
        "newPrice": new_price,
        "discountPct": discount_pct,
        "image": image,
        "url": url,
        "note": note,
    }


def fetch_all():
    state = fetch_initial_state()
    products_state = state["data"]["products"]
    entities = products_state.get("productEntities") or {}
    total_products = (products_state.get("catalogue") or {}).get("data", {}).get("totalProducts")
    print(
        f"Found {len(entities)} fully-detailed products embedded in the /promotions "
        f"page load (totalProducts across the whole listing: {total_products}, "
        f"but the rest requires the disallowed /api/ endpoint — see module docstring)"
    )

    items = []
    for p in entities.values():
        item = parse_entity(p)
        if item:
            items.append(item)
    print(f"Parsed {len(items)} items")

    return items


if __name__ == "__main__":
    products = fetch_all()
    with open("auchan_promocje.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to auchan_promocje.json")
    if not products:
        print(
            "WARNING: 0 items scraped. Either Auchan changed their SSR page's "
            "productEntities shape (check parse_entity()), or the plain GET of "
            "/promotions itself got blocked — see the module docstring's WAF caveat."
        )
