"""
Scraper for Kaufland Poland's online offers page (sklep.kaufland.pl/oferta/).

Unlike kaufland.pl's main marketing site — whose "akcje-promocyjne" page only
links to PDF gazetka flyers with no readable product data — sklep.kaufland.pl
is Kaufland's actual storefront and its offers overview page lists real,
current grocery + household promotions, grouped under category sections
like "Owoce, Warzywa, Rośliny", "Mięso, Drób, Wędliny", "Nabiał",
"Tanio z Kaufland Card XTRA", and so on.

Usage:
    pip install requests beautifulsoup4 --break-system-packages
    python scrape_kaufland.py

Output:
    kaufland_oferta.json — current promo items from sklep.kaufland.pl/oferta/przeglad.html

NOTES ON THIS SCRAPER'S HISTORY:
- The original OFFER_URL (sklep.kaufland.pl/oferta/) started 404ing after
  Kaufland restructured the site; the live page moved to
  /oferta/przeglad.html (confirmed via the "Zobacz ofertę >" link on the
  404 page, and by loading the page live in a browser).
- The category tabs on that page ("Hity tygodnia", "Owoce, Warzywa,
  Rośliny", "Mięso, Drób, Wędliny", ...) are a client-side filter, not
  separate page loads — a plain GET of przeglad.html already returns every
  category's product tiles in one server-rendered HTML document (confirmed
  live: no XHR fires when switching tabs, and all ~29 category sections'
  headings/tiles are present in the DOM before any tab is clicked). So one
  request gets the full offer, no per-category pagination needed.
- Product tiles were inspected directly in devtools and use stable BEM-style
  classes: card = `.k-product-tile`, name = `.k-product-tile__title`,
  variant/subtitle = `.k-product-tile__subtitle`, new price =
  `.k-price-tag__price`, old (crossed-out) price =
  `.k-price-tag__old-price-line-through`, discount badge =
  `.k-price-tag__discount`, image = `.k-product-tile__main-image` (src or
  data-src). Each category's tiles sit under a `.k-product-section` whose
  heading is `.k-product-section__headline_elem`. Products don't carry a
  real per-item URL (the tile's href is just "#"), so `url` in the output
  points at the shared offers page.
- If Kaufland reshuffles these class names again, parse_products_tiles()
  will silently return 0 items — the fallback strategies below (schema.org
  microdata, then a heading-aware text sweep) exist as a safety net, and
  the __main__ block prints a loud warning if everything comes back empty
  so a future run's logs make the breakage obvious.
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
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

# robots.txt (sklep.kaufland.pl/robots.txt) checked 2026-09-02: only
# disallows /etc.clientlibs/ (with an /etc.clientlibs/kaufland exception) —
# /oferta/przeglad.html isn't covered by anything there.
OFFER_URL = "https://sklep.kaufland.pl/oferta/przeglad.html"

PRICE_RE = re.compile(r"\d+[.,]\d{2}\s*(?:zł|PLN)", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"-?\s*(\d{1,3})\s*%")


def parse_price(text):
    if not text:
        return None
    match = re.search(r"(\d+[.,]\d{2})", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_products_tiles(soup):
    """
    Strategy 1 (primary): real `.k-product-tile` cards grouped under
    `.k-product-section` headings — see the module docstring for how these
    class names were confirmed against the live page.
    """
    items = []
    sections = soup.select(".k-product-section")
    for section in sections:
        heading_el = section.select_one(".k-product-section__headline_elem")
        category = heading_el.get_text(strip=True) if heading_el else "Inne"
        if category in ("Aktualna oferta", ""):
            category = "Hity tygodnia"

        for tile in section.select(".k-product-tile"):
            title_el = tile.select_one(".k-product-tile__title")
            if not title_el or not title_el.get_text(strip=True):
                # A handful of live tiles (confirmed: 4 out of 648 on 16 Aug
                # 2026) have a .k-product-tile__title element that's present
                # but empty — likely ad/placeholder slots mixed into the
                # grid. Skip rather than emit a blank product card.
                continue
            name = title_el.get_text(strip=True)
            subtitle_el = tile.select_one(".k-product-tile__subtitle")
            if subtitle_el and subtitle_el.get_text(strip=True):
                name = f"{name} ({subtitle_el.get_text(strip=True)})"

            price_el = tile.select_one(".k-price-tag__price")
            new_price = parse_price(price_el.get_text()) if price_el else None
            if new_price is None:
                continue  # no usable price on this tile — skip rather than guess

            old_price_el = tile.select_one(".k-price-tag__old-price-line-through")
            old_price = parse_price(old_price_el.get_text()) if old_price_el else None

            discount_el = tile.select_one(".k-price-tag__discount")
            discount_pct = None
            if discount_el:
                m = DISCOUNT_RE.search(discount_el.get_text())
                if m:
                    discount_pct = int(m.group(1))
            if discount_pct is None and old_price and new_price:
                discount_pct = round((1 - new_price / old_price) * 100)

            image = None
            img_el = tile.select_one(".k-product-tile__main-image")
            if img_el:
                image = img_el.get("src") or img_el.get("data-src")

            items.append({
                "store": "Kaufland",
                "category": category,
                "name": name,
                "oldPrice": old_price,
                "newPrice": new_price,
                "discountPct": discount_pct,
                "image": image,
                "url": OFFER_URL,
            })
    return items


def parse_products_microdata(soup):
    """Strategy 2: schema.org Product/Offer microdata, kept as a fallback in
    case Kaufland ever adds it — not currently present on the live page."""
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
    Strategy 3 (last resort): heading-aware sweep over visible text, only
    used if both the tile-based and microdata strategies find nothing (i.e.
    Kaufland has changed its markup again). Only trusts REAL <h1>-<h6> tags
    for category boundaries — a short capitalised product name like
    "Nektarynki 1kg" is indistinguishable from a real section heading by
    text shape alone, so guessing headings from text shape mis-tags
    products as categories.
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
    items = parse_products_tiles(soup)
    if not items:
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
            "match the selectors in parse_products_tiles()/parse_products_microdata()/"
            "parse_products_fallback() anymore — open sklep.kaufland.pl/oferta/przeglad.html "
            "in dev tools and adjust them."
        )
