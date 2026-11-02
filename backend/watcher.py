import json
import re

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

PRICE_RE = re.compile(r"[$€£]\s?\d[\d.,]*\d")


def _normalize_price(raw: str) -> float | None:
    """Handle both US (1,234.56) and EU (1.234,56 / 1234,56) number formats.

    Heuristic: the last separator is treated as the decimal point only if it's
    followed by exactly 1-2 digits (a plausible cents amount) and no other
    separator of the same kind follows. A separator followed by 3 digits is
    treated as a thousands grouping instead.
    """
    digits = raw.lstrip("$€£ ").strip()
    last_comma = digits.rfind(",")
    last_dot = digits.rfind(".")
    last_sep_pos = max(last_comma, last_dot)

    decimal_sep = None
    if last_sep_pos != -1:
        trailing_digits = len(digits) - last_sep_pos - 1
        if 1 <= trailing_digits <= 2:
            decimal_sep = digits[last_sep_pos]

    if decimal_sep == ",":
        digits = digits[:last_sep_pos].replace(".", "").replace(",", "") + "." + digits[last_sep_pos + 1:]
    elif decimal_sep == ".":
        digits = digits[:last_sep_pos].replace(".", "").replace(",", "") + "." + digits[last_sep_pos + 1:]
    else:
        digits = digits.replace(",", "").replace(".", "")

    try:
        return float(digits)
    except ValueError:
        return None
OUT_OF_STOCK_PHRASES = [
    "out of stock", "sold out", "currently unavailable", "notify me when available",
    "no longer available", "temporarily out of stock",
]
IN_STOCK_PHRASES = ["add to cart", "add to bag", "buy now", "in stock"]


def _parse_jsonld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict) and ("price" in offers or "availability" in offers):
                price = offers.get("price")
                availability = (offers.get("availability") or "").lower()
                in_stock = "instock" in availability if availability else None
                return {
                    "price": float(price) if price else None,
                    "in_stock": in_stock,
                    "source": "jsonld",
                }
    return None


def _parse_heuristic(soup: BeautifulSoup) -> dict:
    text = soup.get_text(separator=" ").lower()

    in_stock = None
    if any(p in text for p in OUT_OF_STOCK_PHRASES):
        in_stock = False
    elif any(p in text for p in IN_STOCK_PHRASES):
        in_stock = True

    price = None
    matches = PRICE_RE.findall(soup.get_text(separator=" "))
    if matches:
        price = _normalize_price(matches[0])

    return {"price": price, "in_stock": in_stock, "source": "heuristic"}


def check_page(url: str) -> dict:
    """Fetch a product page and best-effort detect price + stock status."""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")

    result = _parse_jsonld(soup)
    if not result or (result["price"] is None and result["in_stock"] is None):
        result = _parse_heuristic(soup)

    result["ok"] = True
    return result
