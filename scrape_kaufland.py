"""
Scraper for Kaufland Poland's online offers page (sklep.kaufland.pl/oferta/).

Unlike kaufland.pl's main marketing site — whose "akcje-promocyjne" page only
links to PDF gazetka flyers with no readable product data — sklep.kaufland.pl
is Kaufland's actual storefront and its /oferta/ page lists real, current
grocery + household promotions as text (fruit & veg, meat, dairy, frozen,
chemia, electronics, clothing, etc.), grouped under category headings like
"Owoce i warzywa", "Mięso, wędliny", "Tanio z Kaufland Card XTRA", and so on.
The page appears to be server-rendered (Adobe AEM-based), not a JS SPA, so a
plain HTTP GET + HTML parse should see the same content a browser does.

Usage:
    pip install requests beautifulsoup4 --break-system-packages
    python scrape_kaufland.py

Output:
    kaufland_oferta.json — current promo items from sklep.kaufland.pl/oferta/

NOTE ON RELIABILITY:
Same caveat as scrape_biedronka.py: this was written from a page-text
extraction (no live DOM/devtools access from this environment), so exact
CSS classes/data-attributes are unknown. parse_products_from_html() tries
schema.org Product microdata first (common on AEM commerce pages for SEO),
then falls back to a heading-aware text sweep that assigns each item to the
nearest preceding short "heading-like" line as its category. Some category
sections use a "Pokaż całą ofertę" (show full offer) expander that may only
render more items via JS click — if item counts look low compared to what
you see in a browser, that's the likely reason; those categories may need a
dedicated per-category URL (check the expander's href/data attributes in
dev tools) rather than being fully covered by this single-page scrape. Run
it once, sanity-check the count/sample items, and let me know what you see
(or connect the Claude-in-Chrome extension) so selectors can be corrected
against the real markup.
"""

import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

OFFER_URL = "https://sklep.kaufland.pl/oferta/"

PRICE_RE = re.compile(r"\d+[.,]\d{2}\s*(?:zł|PLN)", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"-?\s*(\d{1,3})\s*%")


def parse_price(text):
    if not text:
        return None
    match = re.search(r"(\d+[.,]\d{2})", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_products_microdata(soup):
    """Strategy 1: schema.org Product/Offer microdata."""
    items = []
    for node in soup.select('[itemtype*="schema.org/Product"]'):
        name_el = node.select_one('[itemprop="name"]')
        price_el = node.select_one('[itemprop="price"]')
        image_el = node.select_one('[itemprop="image"]')
        link_el = node.select_one('a[href]')
        if not name_el or not price_el:
            continue

        name = name_el.get("content") or name_el.get_text(strip=True)
        try:
            new_price = float(str(price_el.get("content") or price_el.get_text(strip=True)).replace(",", "."))
        except ValueError:
            new_price = parse_price(price_el.get_text())

        old_price = None
        strike_el = node.select_one('.price-old, [class*="strike"], del')
        if strike_el:
            old_price = parse_price(strike_el.get_text())

        items.append({
            "store": "Kaufland",
            "category": "Inne",
            "name": name,
            "oldPrice": old_price,
            "newPrice": new_price,
            "discountPct": (
                round((1 - new_price / old_price) * 100) if old_price and new_price else None
            ),
            "image": image_el.get("content") or image_el.get("src") if image_el else None,
            "url": link_el["href"] if link_el else OFFER_URL,
        })
    return items


def parse_products_fallback(soup):
    """
    Strategy 2: heading-aware sweep over visible text.

    Category tracking only trusts REAL <h1>-<h6> tags (collected up front into
    `heading_texts`) — earlier drafts tried to guess headings from short,
    capitalised lines, but on this kind of page a short capitalised product
    name (e.g. "Nektarynki 1kg") is indistinguishable from a real section
    heading by text shape alone, which mis-tagged products as categories.
    If the live page doesn't mark categories with real heading tags, every
    item just falls back to "Inne" — visibly wrong in a way you can spot,
    rather than silently wrong.
    """
    items = []
    heading_texts = {h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]) if h.get_text(strip=True)}
    lines = [t.strip() for t in soup.get_text("\n").split("\n") if t.strip()]

    current_category = "Inne"
    for i, line in enumerate(lines):
        if line in heading_texts:
            current_category = line
            continue
        if PRICE_RE.search(line):
            prices = PRICE_RE.findall(line)
            discount_match = DISCOUNT_RE.search(line)
            name_candidate = lines[i - 1] if i >= 1 else None
            if not name_candidate or PRICE_RE.search(name_candidate):
                continue

            if len(prices) >= 2:
                old_price, new_price = parse_price(prices[0]), parse_price(prices[1])
            else:
                old_price, new_price = None, parse_price(prices[0])

            discount_pct = int(discount_match.group(1)) if discount_match else (
                round((1 - new_price / old_price) * 100) if old_price and new_price else None
            )

            items.append({
                "store": "Kaufland",
                "category": current_category,
                "name": name_candidate,
                "oldPrice": old_price,
                "newPrice": new_price,
                "discountPct": discount_pct,
                "image": None,
                "url": OFFER_URL,
            })

    return items


def parse_products_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    items = parse_products_microdata(soup)
    if not items:
        items = parse_products_fallback(soup)
    return items


def fetch_all():
    resp = requests.get(OFFER_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return parse_products_from_html(resp.text)


if __name__ == "__main__":
    products = fetch_all()
    with open("kaufland_oferta.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to kaufland_oferta.json")
    if not products:
        print(
            "WARNING: 0 items scraped. The live markup almost certainly doesn't "
            "match the selectors in parse_products_microdata()/parse_products_fallback() "
            "— open sklep.kaufland.pl/oferta/ in dev tools and adjust them."
        )
