"""
Scraper for Stokrotka's grocery/food promotions — sourced via Blix.pl.

BACKGROUND / WHY THIS EXISTS:
Same situation as Biedronka, Żabka and Netto (see scrape_biedronka_food.py's
docstring for the fuller history): Stokrotka's own gazetka viewer only
serves flyer page images with no underlying product API. Blix.pl (a
third-party Polish flyer aggregator, https://blix.pl — not affiliated with
Stokrotka) publishes a short SEO description paragraph alongside every
gazetka issue it lists for Stokrotka too, using the exact same markup
pattern already proven for Biedronka/Żabka/Netto:

    "Znajdziesz w niej m.in. oferty na: <strong>Whisky Grant's Triple
    Wood</strong> za <strong>39,99zł</strong>, <strong>Wino bezalkoholowe
    Cin&Cin Sauvignon Blanc</strong> za <strong>19,99zł</strong>, ..."

Confirmed live against https://blix.pl/sklep/stokrotka/ and one of its
gazetka issue pages while building this — same clean <strong>name</strong>
za <strong>price zł</strong> pattern, same server-rendered guarantee (a
plain `requests.get` sees it, no JS needed). Note: not every Stokrotka
gazetka issue has this description paragraph (some come back empty) —
same variance already seen with Biedronka/Żabka issues, handled the same
way here (skip issues with no matches, keep going).

IMPORTANT — READ BEFORE RELYING ON THIS DATA:
- This is NOT Stokrotka's full catalog — same curated-highlight-list caveat
  as scrape_biedronka_food.py / scrape_zabka.py / scrape_netto.py. Expect on
  the order of 100-200 unique items across all currently active issues, not
  a full product-grid scrape.
- The data is sourced from Blix's own editorial copy about Stokrotka's
  flyers, not from Stokrotka directly — each item's `url` points at the
  specific Blix gazetka page it came from.
- Only the "current" price is available this way — oldPrice/discountPct are
  always null for every item here.
- Stokrotka runs several parallel flyer lines (Supermarket, Market, Express,
  seasonal "Hity na start" / "Wielki przewrót cenowy" etc.) that mix
  groceries, alcohol and household items with no reliable per-item category
  signal in this text, so everything here is tagged "Spożywcze i inne"
  (groceries & other), matching the other blix.pl-sourced tabs' approach.

Usage:
    pip install requests --break-system-packages
    python scrape_stokrotka.py

Output:
    stokrotka_gazetka.json — highlighted current-price items from
    Stokrotka's active gazetka issues, via Blix.pl
"""

import json
import re
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

BASE_URL = "https://blix.pl"
SHOP_PATH = "/sklep/stokrotka/"
STORE_NAME = "Stokrotka"
MAX_ISSUES = 60  # safety cap, same rationale as scrape_biedronka_food.py

GAZETKA_ID_RE = re.compile(r"/sklep/stokrotka/gazetka/(\d+)/")
NAME_PRICE_RE = re.compile(
    r"<strong>([^<]+)</strong>\s*za\s*<strong>(\d+),(\d{2})\s*z[łl]</strong>",
    re.IGNORECASE,
)


def discover_gazetka_ids():
    """The Stokrotka shop page on Blix links to every currently-listed
    gazetka issue — collect the unique numeric IDs, in the order they
    first appear on the page. Mirrors scrape_biedronka_food.py's
    discover_gazetka_ids()."""
    resp = requests.get(BASE_URL + SHOP_PATH, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    ids = []
    seen = set()
    for match in GAZETKA_ID_RE.finditer(resp.text):
        gid = match.group(1)
        if gid not in seen:
            seen.add(gid)
            ids.append(gid)
    return ids[:MAX_ISSUES]


def parse_gazetka_description(html, gazetka_url):
    """Pull (name, price) pairs out of the <strong>name</strong> za
    <strong>price zł</strong> pattern in a gazetka page's SEO description."""
    items = []
    for name, zloty, grosz in NAME_PRICE_RE.findall(html):
        name = name.strip()
        if not name:
            continue
        try:
            price = float(f"{zloty}.{grosz}")
        except ValueError:
            continue
        items.append({
            "store": STORE_NAME,
            "category": "Spożywcze i inne",
            "name": name,
            "oldPrice": None,
            "newPrice": price,
            "discountPct": None,
            "image": None,
            "url": gazetka_url,
            "note": "hit z gazetki — dane za pośrednictwem blix.pl",
        })
    return items


def fetch_all():
    gazetka_ids = discover_gazetka_ids()
    print(f"Found {len(gazetka_ids)} active {STORE_NAME} gazetka issues on Blix")

    all_items = []
    seen_keys = set()
    for gid in gazetka_ids:
        url = f"{BASE_URL}{SHOP_PATH}gazetka/{gid}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"  gazetka {gid}: request failed ({exc}), skipping")
            continue

        items = parse_gazetka_description(resp.text, url)
        new_count = 0
        for item in items:
            key = (item["name"], item["newPrice"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_items.append(item)
            new_count += 1
        print(f"  gazetka {gid}: {len(items)} mentioned, {new_count} new")

        time.sleep(0.5)  # be polite to Blix's servers

    return all_items


if __name__ == "__main__":
    products = fetch_all()
    with open("stokrotka_gazetka.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to stokrotka_gazetka.json")
    if not products:
        print(
            "WARNING: 0 items scraped. Blix.pl may have changed their gazetka "
            "page layout or the description-paragraph pattern — open "
            "blix.pl/sklep/stokrotka/ and a gazetka issue page in dev tools "
            "and check the parse_gazetka_description() pattern in this script."
        )
