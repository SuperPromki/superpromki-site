"""
Scraper for Lidl Poland's product API — covers two feeds:

1. wyprzedaz (clearance)  — non-food items only (home, workshop/garden, sport,
   fashion, baby). ~189 products.
2. search?category.id=10068374 — "Żywność i napoje" (Food & Drinks), actual
   fresh groceries plus packaged food/drinks. ~89 products.

Note: Lidl still doesn't sell most fresh produce/meat/dairy through a general
weekly-flyer feed — this "search" endpoint returns whatever is CURRENTLY
promoted under the food category, which changes day to day. Some prices here
require the Lidl Plus app coupon or a minimum purchase quantity — see the
"requiresCoupon" / "note" fields in the output.

Usage:
    pip install requests --break-system-packages
    python scrape_lidl.py

Output:
    lidl_wyprzedaz.json  — non-food clearance items
    lidl_food.json       — food & drinks items
"""

import json
import time
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

FETCH_SIZE = 48


def fetch_all(url, base_params, item_parser):
    """Generic paginator: GETs `url` with offset stepping until numFound is exhausted."""
    items_out = []
    offset = 0
    total = None

    while total is None or offset < total:
        params = {**base_params, "offset": offset}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if total is None:
            total = data.get("numFound", 0)
            print(f"{url.split('/')[-1]}: total available = {total}")

        raw_items = data.get("items", [])
        if not raw_items:
            break

        for raw in raw_items:
            parsed = item_parser(raw)
            if parsed:
                items_out.append(parsed)

        offset += FETCH_SIZE
        time.sleep(0.5)  # be polite — don't hammer the endpoint

    return items_out


def parse_wyprzedaz_item(item):
    """Non-food clearance item (simple price/discount shape)."""
    try:
        gridbox = item["gridbox"]["data"]
        price = gridbox["price"]
        discount = price.get("discount", {})

        return {
            "store": "Lidl",
            "category": gridbox["keyfacts"].get("wonCategoryPrimary", "").split("/")[-1] or "Inne",
            "name": gridbox["fullTitle"],
            "oldPrice": price.get("oldPrice"),
            "newPrice": price.get("price"),
            "discountPct": discount.get("percentageDiscount"),
            "image": gridbox.get("image"),
            "url": "https://www.lidl.pl" + gridbox["canonicalUrl"],
        }
    except (KeyError, TypeError):
        return None


def parse_food_item(item):
    """
    Food item — price can live in two different places:
      - gridbox.data.price / .discount   (regular multi-buy discounts, e.g. cheese)
      - gridbox.data.lidlPlus[0].price    (Lidl Plus app-only coupon prices, e.g. meat)
    """
    try:
        gridbox = item["gridbox"]["data"]
        root_price = gridbox.get("price", {})
        lidl_plus = gridbox.get("lidlPlus", [])

        requires_coupon = bool(lidl_plus) and root_price.get("price") is None
        price_block = lidl_plus[0]["price"] if requires_coupon else root_price
        discount = price_block.get("discount", {})

        category = "Inne"
        breadcrumbs = gridbox.get("wonCategoryPrimary", "")
        # category name usually sits in meta.wonCategoryBreadcrumbs on the outer item, fall back gracefully
        keyfacts_cat = gridbox.get("keyfacts", {}).get("wonCategoryPrimary", "")
        if "/" in keyfacts_cat:
            category = keyfacts_cat.split("/")[-1]

        note_parts = []
        if requires_coupon:
            note_parts.append("wymaga aplikacji Lidl Plus")
        packaging_text = price_block.get("packaging", {}).get("text", "")
        if "zakupie" in packaging_text:
            # pull out just the "przy zakupie N ..." fragment if present
            idx = packaging_text.find("zakupie")
            note_parts.append(packaging_text[max(0, idx - 3): idx + 20].strip(" .*"))

        return {
            "store": "Lidl",
            "category": category,
            "name": gridbox["fullTitle"],
            "oldPrice": price_block.get("oldPrice"),
            "newPrice": price_block.get("price"),
            "discountPct": discount.get("percentageDiscount"),
            "note": "; ".join(note_parts) if note_parts else None,
            "image": gridbox.get("image"),
            "url": "https://www.lidl.pl" + gridbox["canonicalUrl"],
        }
    except (KeyError, TypeError, IndexError):
        return None


if __name__ == "__main__":
    non_food = fetch_all(
        "https://www.lidl.pl/q/api/query/wyprzedaz",
        {"locale": "pl_PL", "assortment": "PL", "version": "2.1.0", "fetchsize": FETCH_SIZE},
        parse_wyprzedaz_item,
    )
    with open("lidl_wyprzedaz.json", "w", encoding="utf-8") as f:
        json.dump(non_food, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(non_food)} non-food items to lidl_wyprzedaz.json")

    food = fetch_all(
        "https://www.lidl.pl/q/api/search",
        {
            "locale": "pl_PL", "assortment": "PL", "version": "2.1.0",
            "fetchsize": FETCH_SIZE, "sort": "storeStartDate-desc",
            "category.id": "10068374",
        },
        parse_food_item,
    )
    with open("lidl_food.json", "w", encoding="utf-8") as f:
        json.dump(food, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(food)} food items to lidl_food.json")

