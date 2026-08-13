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

If Biedronka ever exposes real grocery-promo data (or you get hold of an
internal API), swap PROMO_URL / parse_tile() below — the JSON output shape
is designed to match scrape_lidl.py so the frontend doesn't need changes.

Usage:
    pip install requests beautifulsoup4 --break-system-packages
    python scrape_biedronka.py

Output:
    biedronka_home.json — non-food promo items from home.biedronka.pl

NOTE ON RELIABILITY:
This was written from external research (page text extracted via a fetch
tool), not a live look at the rendered DOM — I could not inspect the actual
HTML/CSS in this environment (no browser access, no direct internet from
the sandbox shell). The selector strategy below tries schema.org Product
microdata first (common on SFCC storefronts for SEO), then falls back to a
regex sweep over visible text. Run it once, check the item count and a
few sample entries, and if it comes back empty or wrong, that almost
certainly means the live markup doesn't match these guesses — open the
page's dev tools (Network/Elements tab), find the real product-tile
selector, and adjust `parse_products_from_html()` accordingly. Happy to
help fix it once you can share what the real markup looks like, or once
the Claude-in-Chrome browser extension is connected so I can inspect it
directly.
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

BASE_URL = "https://home.biedronka.pl"
PROMO_PATH = "/promocje/"
PAGE_SIZE = 60
MAX_PAGES = 20  # safety cap so a parsing bug can't loop forever


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


def parse_products_microdata(soup):
    """Strategy 1: schema.org Product/Offer microdata (common on SFCC for SEO)."""
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

        # Old/strike-through price usually sits nearby with a "strike"/"old" class —
        # best-effort search within the same tile.
        old_price = None
        strike_el = node.select_one('.price-standard, .strike-through, [class*="strike"], del')
        if strike_el:
            old_price = parse_price(strike_el.get_text())

        items.append({
            "store": "Biedronka",
            "category": "Dom i ogród",  # Biedronka Home doesn't expose category text
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
    Strategy 2 (fallback): sweep visible text for "<name> ... <old> zł ... <new> zł"
    patterns. Much less precise than real selectors — only used if microdata
    parsing finds nothing, so at least *something* comes out for a first pass.
    """
    items = []
    text_blocks = soup.get_text("\n").split("\n")
    text_blocks = [t.strip() for t in text_blocks if t.strip()]

    price_pattern = re.compile(r"\d+[.,]\d{2}\s*z[łl]")
    for i, line in enumerate(text_blocks):
        if price_pattern.search(line) and i >= 1:
            # assume the product name is 1-2 lines above the first price we see
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
            "match the selectors in parse_products_microdata()/parse_products_fallback() "
            "— open home.biedronka.pl/promocje/ in dev tools and adjust them."
        )
