"""
Scraper for Biedronka's actual grocery/food promotions — sourced via Blix.pl.

BACKGROUND / WHY THIS EXISTS:
Biedronka's own gazetka flyer viewer (biedronka.pl/pl/gazetki) only serves
page images with no underlying product API, and OCR on those flyer images
was tried in an earlier pass and rejected as unreliable (digit misreads —
e.g. a price badge's "2" got read as "9" depending on exactly how the crop
was framed). The "Moja Biedronka" app has real product data but requires
phone-number + SMS login, which isn't safe or reliable to automate in a
scheduled job. See scrape_biedronka.py's docstring for the fuller history —
that scraper covers Biedronka Home (non-food) instead, for the same reason.

This scraper takes a different path: Blix.pl (a third-party Polish flyer
aggregator, https://blix.pl — not affiliated with Biedronka) publishes a
short SEO description paragraph alongside every gazetka issue it lists, e.g.:

    "Znajdziesz w niej m.in. oferty na: <strong>Ser żółty Gouda
    Światowid</strong> za <strong>14,99zł</strong>, <strong>Olej
    rzepakowy Wielkopolski</strong> za <strong>4,99zł</strong>, ..."

Both the product name and the price are wrapped in their own <strong> tag
with a fixed "za" (for) connecting them — a clean, unambiguous pattern to
regex out, and confirmed server-rendered (a plain `requests.get` sees it,
no JS/browser needed) across multiple live gazetka issues while building
this.

IMPORTANT — READ BEFORE RELYING ON THIS DATA:
- This is NOT Biedronka's full catalog. It's a curated highlight list Blix
  writes per flyer issue — anywhere from 0 to ~16 named products per issue
  (some issues, e.g. non-grocery ones, mention none). Expect on the order
  of 100-200 unique items total across all currently active issues, not
  the 1000+ a full product-grid scrape would give (like scrape_kaufland.py
  or scrape_biedronka.py get from their stores' own listing pages).
- The data is sourced from Blix's own editorial copy about Biedronka's
  flyers, not from Biedronka directly. Each item's `url` points at the
  specific Blix gazetka page it came from, so Blix is credited as the
  source the same way every other scraper's items link back to the page
  they were read from.
- Only the "current" price is available this way — there's no separate
  strikethrough/old price or discount percentage in this text, so
  oldPrice/discountPct are always null for every item here.
- Not everything mentioned in a flyer's description is food — Biedronka's
  general gazetki mix in household items (e.g. fabric softener) alongside
  groceries, and this text has no reliable per-item category signal. So
  everything here is tagged "Spożywcze i inne" (groceries & other) rather
  than claimed as pure food — see promocje.html's footer note for how
  this is explained to visitors.

Usage:
    pip install requests --break-system-packages
    python scrape_biedronka_food.py

Output:
    biedronka_gazetka.json — highlighted current-price items from Biedronka's
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
SHOP_PATH = "/sklep/biedronka/"
MAX_ISSUES = 60  # safety cap — Blix currently lists ~30 active Biedronka issues

GAZETKA_ID_RE = re.compile(r"/sklep/biedronka/gazetka/(\d+)/")
NAME_PRICE_RE = re.compile(
    r"<strong>([^<]+)</strong>\s*za\s*<strong>(\d+),(\d{2})\s*z[łl]</strong>",
    re.IGNORECASE,
)


def discover_gazetka_ids():
    """The Biedronka shop page on Blix links to every currently-listed
    gazetka issue — collect the unique numeric IDs, in the order they
    first appear on the page."""
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
    <strong>price zł</strong> pattern in a gazetka page's SEO description —
    see the module docstring for how this was confirmed against the live
    markup."""
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
            "store": "Biedronka",
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
    print(f"Found {len(gazetka_ids)} active Biedronka gazetka issues on Blix")

    all_items = []
    seen_keys = set()
    for gid in gazetka_ids:
        url = f"{BASE_URL}/sklep/biedronka/gazetka/{gid}/"
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
    with open("biedronka_gazetka.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(products)} items to biedronka_gazetka.json")
    if not products:
        print(
            "WARNING: 0 items scraped. Blix.pl may have changed their gazetka "
            "page layout or the description-paragraph pattern — open "
            "blix.pl/sklep/biedronka/ and a gazetka issue page in dev tools "
            "and check the parse_gazetka_description() pattern in this script."
        )
