"""
Scraper for Biedronka Home (home.biedronka.pl) — Biedronka's own online shop.

IMPORTANT LIMITATION — read this before relying on the output:
Biedronka's actual grocery promotions (fresh food, weekly "gazetka" flyers)
are NOT available through any public, unauthenticated endpoint. They live
either in image/PDF flyers (biedronka.pl/pl/gazetki — a JS-rendered flyer
viewer with no discoverable data API) or inside the "Moja Biedronka" mobile
app, which requires phone-number + SMS login. Neither is safe or reliable
to automate in a GitHub Actions workflow.

"Biedronka Home" (home.biedronka.pl) is Biedronka's *separate* online store
for home, garden, electronics, and lifestyle goods — NOT groceries. It's
built on Salesforce Commerce Cloud (Demandware), which server-renders
product grids, so it's actually scrapable. This mirrors the same
non-food-only limitation the Lidl scraper already has for lidl.pl/q/api/query/wyprzedaz.

Usage:
    pip install requests beautifulsoup4 --break-system-packages
    python scrape_biedronka.py

Output:
    biedronka_home.json — non-food promo items from home.biedronka.pl

NOTES ON THIS SCRAPER'S HISTORY:
- Originally written blind (no live DOM access) and only had a schema.org
  microdata strategy + a regex text-sweep fallback — both came back with 0
  items once actually run, because the real page uses neither: it's a
  Salesforce Commerce Cloud storefront with its own BEM-style classes and
  no Product microdata.
- Inspected the live page directly in devtools and confirmed (via a raw
  `fetch()`, not just the post-JS DOM) that /promocje/?start=N&sz=60 returns
  fully server-rendered product tiles — no JS execution needed. Each tile is
  a `div.product-tile.js-product-tile` with: name in `.product-tile__name`,
  brand in `.product-tile__brand-name`, current price in `.price-tile__sales`
  and original price in `.price-tile__standard` (both split across a bare
  text node for the whole-zloty part and a nested `.price-tile__decimal`
  span for the grosze part — e.g. "149" + <span>00</span> = 149.00 zł, with
  no separator character between them in the raw text), product link on
  `.product-tile-clickable`'s nearest `a[href]`, and image `src` on the
  tile's `<img>`. There's no per-tile category text in the markup (category
  only shows up as sidebar facet counts), so every item is tagged
  "Dom i ogród" to match what this whole page covers (matches the site's
  own "Biedronka Home (tylko dom i ogród)" framing in promocje.html).
- parse_products_tiles() (below) implements this and is now the primary
  strategy; the old microdata/text-sweep strategies stay as a safety net
  in case Biedronka changes the markup again.
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup, NavigableString

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

# robots.txt (home.biedronka.pl/robots.txt) checked 2026-09-02: disallows a
# long list of demandware account/checkout/search paths and query patterns
# (e.g. /cart, /wishlist, /search, /*q=*) — /promocje/ itself, and the plain
# ?start=N&sz=M pagination this scraper uses, aren't among them.
BASE_URL = "https://home.biedronka.pl"
PROMO_PATH = "/promocje/"
PAGE_SIZE = 60
MAX_PAGES = 30  # safety cap so a parsing bug can't loop forever (1697 products / 60 ≈ 29 pages)


def fetch_page(start):
    """SFCC storefronts paginate search/grid pages with ?start=N&sz=M."""
    params = {"start": start, "sz": PAGE_SIZE}
    resp = requests.get(BASE_URL + PROMO_PATH, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_price(text):
    """'149,00 zł' / '149.00 zł' -> 149.0"""
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").strip()
    match = re.search(r"(\d+[.,]\d{2})", cleaned)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_tile_price(price_el):
    """
    Pulls a price out of a `.price-tile__sales` / `.price-tile__standard`
    element, whose raw structure is a bare text node for the whole-zloty
    part followed by a nested `.price-tile__decimal` span for the grosze
    part (e.g. "149" + <span>00</span>), with no separator between them —
    see the module docstring for how this was confirmed against the live
    markup.
    """
    if not price_el:
        return None
    whole = None
    for content in price_el.contents:
        if isinstance(content, NavigableString):
            text = content.strip()
            if text:
                whole = re.sub(r"[^\d]", "", text)
                if whole:
                    break
    if not whole:
        return None
    decimal_el = price_el.select_one(".price-tile__decimal")
    decimal = re.sub(r"[^\d]", "", decimal_el.get_text()) if decimal_el else "00"
    decimal = (decimal or "00")[:2].ljust(2, "0")
    try:
        return float(f"{whole}.{decimal}")
    except ValueError:
        return None


def parse_products_tiles(soup):
    """Strategy 1 (primary): real `.product-tile` cards — see module docstring."""
    items = []
    for tile in soup.select(".product-tile.js-product-tile"):
        name_el = tile.select_one(".product-tile__name")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        brand_el = tile.select_one(".product-tile__brand-name")
        if brand_el and brand_el.get_text(strip=True):
            name = f"{brand_el.get_text(strip=True)} {name}"

        new_price = extract_tile_price(tile.select_one(".price-tile__sales"))
        if new_price is None:
            continue  # no usable price on this tile — skip rather than guess
        old_price = extract_tile_price(tile.select_one(".price-tile__standard"))

        discount_pct = (
            round((1 - new_price / old_price) * 100) if old_price and new_price else None
        )

        link_el = tile.select_one("a[href]")
        url = None
        if link_el and link_el.get("href"):
            href = link_el["href"]
            url = BASE_URL + href if href.startswith("/") else href

        img_el = tile.select_one("img")
        image = None
        if img_el:
            image = img_el.get("src") or img_el.get("data-src")
            if image:
                image = image.split("?")[0]

        items.append({
            "store": "Biedronka",
            "category": "Dom i ogród",
            "name": name,
            "oldPrice": old_price,
            "newPrice": new_price,
            "discountPct": discount_pct,
            "image": image,
            "url": url or (BASE_URL + PROMO_PATH),
        })
    return items


def parse_products_microdata(soup):
    """Strategy 2: schema.org Product/Offer microdata, kept as a fallback —
    not present on the live SFCC markup as of this writing."""
    items = []
    for node in soup.select('[itemtype*="schema.org/Product"]'):
        name_el = node.select_one('[itemprop="name"]')
        price_el = node.select_one('[itemprop="price"]')
        image_el = node.select_one('[itemprop="image"]')
        link_el = node.select_one('a[href]')
        if not name_el or not price_el:
            continue

        name = name_el.get("content") or name_el.get_text(strip=True)
        new_price = price_el.get("content") or price_el.get_text(strip=True)
        try:
            new_price = float(str(new_price).replace(",", "."))
        except ValueError:
            new_price = parse_price(str(new_price))

        old_price = None
        strike_el = node.select_one('.price-standard, .strike-through, [class*="strike"], del')
        if strike_el:
            old_price = parse_price(strike_el.get_text())

        items.append({
            "store": "Biedronka",
            "category": "Dom i ogród",
            "name": name,
            "oldPrice": old_price,
            "newPrice": new_price,
            "discountPct": (
                round((1 - new_price / old_price) * 100) if old_price and new_price else None
            ),
            "image": image_el.get("content") or image_el.get("src") if image_el else None,
            "url": BASE_URL + link_el["href"] if link_el and link_el["href"].startswith("/") else (link_el["href"] if link_el else None),
        })
    return items


def parse_products_fallback(soup):
    """
    Strategy 3 (last resort): sweep visible text for "<name> ... <old> zł ...
    <new> zł" patterns. Much less precise than real selectors — only used if
    both strategies above find nothing.
    """
    items = []
    text_blocks = soup.get_text("\n").split("\n")
    text_blocks = [t.strip() for t in text_blocks if t.strip()]

    price_pattern = re.compile(r"\d+[.,]\d{2}\s*z[łl]")
    for i, line in enumerate(text_blocks):
        if price_pattern.search(line) and i >= 1:
            name_candidate = text_blocks[i - 1]
            if len(name_candidate) < 5 or price_pattern.search(name_candidate):
                continue
            prices_in_line = price_pattern.findall(line)
            if len(prices_in_line) >= 2:
                old_price = parse_price(prices_in_line[0])
                new_price = parse_price(prices_in_line[1])
            elif len(prices_in_line) == 1:
                old_price = None
                new_price = parse_price(prices_in_line[0])
            else:
                continue
            items.append({
                "store": "Biedronka",
                "category": "Dom i ogród",
                "name": name_candidate,
                "oldPrice": old_price,
                "newPrice": new_price,
                "discountPct": (
                    round((1 - new_price / old_price) * 100) if old_price and new_price else None
                ),
                "image": None,
                "url": BASE_URL + PROMO_PATH,
            })
    return items


def parse_products_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    items = parse_products_tiles(soup)
    if not items:
        items = parse_products_microdata(soup)
    if not items:
        items = parse_products_fallback(soup)
    return items


def fetch_all():
    all_items = []
    seen_names = set()
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        html = fetch_page(start)
        items = parse_products_from_html(html)
        if not items:
            break

        new_count = 0
        for item in items:
            key = (item["name"], item["newPrice"])
            if key in seen_names:
                continue
            seen_names.add(key)
            all_items.append(item)
            new_count += 1

        print(f"page start={start}: found {len(items)} tiles, {new_count} new")
        if new_count == 0:
            break  # stop once a page brings nothing new (avoids infinite loop on bad pagination)

        time.sleep(0.5)  # be polite

    return all_items


if __name__ == "__main__":
    products = fetch_all()
    with open("biedronka_home.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to biedronka_home.json")
    if not products:
        print(
            "WARNING: 0 items scraped. The live markup almost certainly doesn't "
            "match the selectors in parse_products_tiles()/parse_products_microdata()/"
            "parse_products_fallback() anymore — open home.biedronka.pl/promocje/ "
            "in dev tools and adjust them."
        )
