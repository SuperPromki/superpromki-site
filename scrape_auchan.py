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
<script> tag. That blob already contains, with no JS execution needed:
  - `session.csrf.token` — a CSRF token required by the site's own API
  - `data.products.catalogue.data.productGroups[0].products` — the full
    list of product IDs (UUIDs) in the current promotions listing (~300)

The page itself only renders product NAME + price for the first ~50 tiles;
the rest of each product's details (price, promo price, category, image)
come from a second call the site's own JS makes as you scroll:
    PUT https://zakupy.auchan.pl/api/webproductpagews/v6/products
    body: JSON array of product-id strings
    header: X-CSRF-TOKEN: <token from __INITIAL_STATE__>
This returns full product objects (name, price, promoPrice, categoryPath,
images, promotions[]) for whatever IDs you send it — confirmed working via
a direct fetch() using only the token pulled from a plain HTML fetch, no
browser-only APIs involved. That's the pipeline this scraper replicates:
  1. GET /promotions, extract __INITIAL_STATE__ from the response HTML
  2. Pull out the CSRF token + the full product-id list from that same JSON
  3. PUT the id list to the products API in batches, collect full details

CSS-class-based scraping was ruled out: Auchan's product tiles are built
with styled-components, whose classes (e.g. "sc-mmemlz-0 fEEncK") are
hashed per build and not stable across deploys — see scrape_kaufland.py /
scrape_biedronka.py for how BEM-style stable classes were used there
instead; Auchan's markup doesn't offer that, hence this API-based approach.

IMPORTANT — READ BEFORE RELYING ON THIS DATA:
- This covers whatever product-id list is embedded in the /promotions
  page's initial server-rendered payload — confirmed to be ~300 items at
  the time this was built (there's a `nextPageToken` suggesting more may
  exist beyond that; this scraper does not currently page past the initial
  list, matching the "list what one page/load gives you" scope every other
  scraper in this repo also settles for).
- zakupy.auchan.pl sits behind AWS WAF (a `mp_verify` bot-detection request
  fires on page load, and there's an `aws-waf-token` cookie). The plain
  `requests` calls here worked fine when this was built, but if this step
  starts failing in CI, bot-detection blocking the datacenter/CI IP — same
  risk noted in scrape_lidl.py — is the most likely reason. It's marked
  continue-on-error in the workflow for that reason.
- Product URLs are best-effort: Auchan's real product URLs are
  /products/<slugified-name>/<retailerProductId>, and the slug is rebuilt
  here rather than read from the API (which doesn't return it directly).
  If the rebuilt slug doesn't exactly match Auchan's own, the numeric ID at
  the end of the URL should still be enough for their site to resolve it.

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
PROMOTIONS_PATH = "/promotions"
PRODUCTS_API = "/api/webproductpagews/v6/products"
BATCH_SIZE = 30  # conservative — only ever confirmed the API with small batches while building this

STATE_PREFIX_RE = re.compile(r"^\s*window\.__INITIAL_STATE__\s*=\s*")


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
    """GET the promotions page and pull the embedded __INITIAL_STATE__ JSON
    out of its <script> tag — see module docstring for how this was
    confirmed to already contain everything needed, no JS execution."""
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


def extract_price(price_obj):
    if not price_obj or price_obj.get("amount") is None:
        return None
    try:
        return float(price_obj["amount"])
    except (TypeError, ValueError):
        return None


def parse_product(p):
    name = p.get("name")
    if not name:
        return None

    old_price = extract_price(p.get("price"))
    new_price = extract_price(p.get("promoPrice")) or old_price
    if new_price is None:
        return None
    if old_price is not None and new_price is not None and old_price <= new_price:
        old_price = None  # not actually discounted — don't show a fake strikethrough

    discount_pct = (
        round((1 - new_price / old_price) * 100) if old_price and new_price else None
    )

    # Only surface the raw promo description as a note when we couldn't cleanly
    # derive a discount % from price/promoPrice (e.g. bundle deals like
    # "2+1 za grosz") — otherwise it'd just duplicate the discount badge.
    promotions = p.get("promotions") or []
    note = promotions[0]["description"] if promotions and discount_pct is None else None

    category_path = p.get("categoryPath") or []
    category = category_path[1] if len(category_path) > 1 else (
        category_path[0] if category_path else "Inne"
    )

    images = p.get("images") or []
    image = images[0]["src"] if images and images[0].get("src") else None

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
    token = state["session"]["csrf"]["token"]
    catalogue = state["data"]["products"]["catalogue"]["data"]
    product_ids = catalogue["productGroups"][0]["products"]
    total_products = catalogue.get("totalProducts")
    print(f"Found {len(product_ids)} product IDs in the promotions listing (totalProducts={total_products})")

    api_headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-CSRF-TOKEN": token,
    }

    items = []
    for i in range(0, len(product_ids), BATCH_SIZE):
        batch = product_ids[i:i + BATCH_SIZE]
        try:
            resp = requests.put(
                BASE_URL + PRODUCTS_API,
                headers=api_headers,
                data=json.dumps(batch),
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            print(f"  batch {i}-{i+len(batch)}: request failed ({exc}), skipping")
            continue
        except ValueError:
            print(f"  batch {i}-{i+len(batch)}: response wasn't valid JSON, skipping")
            continue

        batch_items = 0
        for p in data.get("products", []):
            item = parse_product(p)
            if item:
                items.append(item)
                batch_items += 1
        print(f"  batch {i}-{i+len(batch)}: {batch_items} items parsed")

    return items


if __name__ == "__main__":
    products = fetch_all()
    with open("auchan_promocje.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to auchan_promocje.json")
    if not products:
        print(
            "WARNING: 0 items scraped. Either Auchan changed their SSR/API setup "
            "(check fetch_initial_state()'s brace-matching and the products API "
            "response shape), or their WAF blocked this run's IP — see the "
            "module docstring's WAF caveat."
        )
