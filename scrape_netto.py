"""
Scraper for Netto's grocery/food promotions — sourced via Blix.pl.

BACKGROUND / WHY THIS EXISTS:
Same situation as Biedronka and Żabka (see scrape_biedronka_food.py's
docstring for the fuller history): Netto's own gazetka viewer only serves
flyer page images with no underlying product API. Blix.pl (a third-party
Polish flyer aggregator, https://blix.pl — not affiliated with Netto)
publishes a short SEO description paragraph alongside every gazetka issue
it lists for Netto too, using the exact same markup pattern already proven
for Biedronka/Żabka:

    "Znajdziesz w niej m.in. oferty na: <strong>Kalafior</strong> za
    <strong>5,99zł</strong>, <strong>Winogrona jasne</strong> za
    <strong>5,99zł</strong>, ..."

Confirmed live against https://blix.pl/sklep/netto/ and one of its gazetka
issue pages while building this — same clean <strong>name</strong> za
<strong>price zł</strong> pattern, same server-rendered guarantee (a plain
`requests.get` sees it, no JS needed).

IMPORTANT — READ BEFORE RELYING ON THIS DATA:
- This is NOT Netto's full catalog — same curated-highlight-list caveat as
  scrape_biedronka_food.py / scrape_zabka.py. Expect on the order of
  100-200 unique items across all currently active issues, not a full
  product-grid scrape.
- The data is sourced from Blix's own editorial copy about Netto's flyers,
  not from Netto directly — each item's `url` points at the specific Blix
  gazetka page it came from.
- Only the "current" price is available this way — oldPrice/discountPct are
  always null for every item here.
- Netto's gazetki mix in groceries, alcohol, and household/DIY "inspiracje"
  items with no reliable per-item category signal in this text, so
  everything here is tagged "Spożywcze i inne" (groceries & other), matching
  the Biedronka/Żabka gazetka tabs' approach.

Usage:
    pip install requests --break-system-packages
    python scrape_netto.py

Output:
    netto_gazetka.json — highlighted current-price items from Netto's
    active gazetka issues, via Blix.pl
"""

import json
import re
import time

import requests

# Identifies honestly as a bot rather than spoofing a real browser — unlike
# the store-facing scrapers (Lidl/Kaufland/Biedronka Home/Auchan), which
# need browser-like headers just to get past those sites' bot detection
# (see their own docstrings), blix.pl has no such gate, so there's no
# functional cost to being transparent here, and it's better practice:
# whoever looks at blix.pl's access logs can see exactly what's hitting
# them and why, rather than something pretending to be Chrome.
# robots.txt (blix.pl/robots.txt) checked 2026-09-02: only disallows
# /lista-zakupow/, /shoppinglist/*, /api/* — none of which this touches.
HEADERS = {
    "User-Agent": (
        "SuperPromkiBot/1.0 (+https://superpromki.github.io/superpromki-site/; "
        "niekomercyjne, hobbystyczne porównanie cen; kontakt przez GitHub Issues "
        "na github.com/SuperPromki/superpromki-site)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

BASE_URL = "https://blix.pl"
SHOP_PATH = "/sklep/netto/"
STORE_NAME = "Netto"
MAX_ISSUES = 60  # safety cap, same rationale as scrape_biedronka_food.py

GAZETKA_ID_RE = re.compile(r"/sklep/netto/gazetka/(\d+)/")
NAME_PRICE_RE = re.compile(
    r"<strong>([^<]+)</strong>\s*za\s*<strong>(\d+),(\d{2})\s*z[łl]</strong>",
    re.IGNORECASE,
)


def discover_gazetka_ids():
    """The Netto shop page on Blix links to every currently-listed gazetka
    issue — collect the unique numeric IDs, in the order they first appear
    on the page. Mirrors scrape_biedronka_food.py's discover_gazetka_ids()."""
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
    with open("netto_gazetka.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to netto_gazetka.json")
    if not products:
        print(
            "WARNING: 0 items scraped. Blix.pl may have changed their gazetka "
            "page layout or the description-paragraph pattern — open "
            "blix.pl/sklep/netto/ and a gazetka issue page in dev tools "
            "and check the parse_gazetka_description() pattern in this script."
        )
