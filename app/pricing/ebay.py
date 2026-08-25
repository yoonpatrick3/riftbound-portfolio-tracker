from datetime import datetime, timedelta, timezone
from statistics import mean, median
import base64
import re
from typing import Optional

import requests

from ..models import Asset, PriceResult

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SOLD_SEARCH_URL = "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

BASIC_SCOPE = "https://api.ebay.com/oauth/api_scope"
SOLD_SCOPE = "https://api.ebay.com/oauth/api_scope/commerce.marketplace.insights.readonly"


class EbaySoldPricingProvider:
    """
    eBay pricing provider.

    Preferred path:
      1. Marketplace Insights sold comps, when the eBay app has access.
      2. Browse API fixed-price active listings as a supported fallback.

    Marketplace Insights is restricted by eBay, so normal developer apps may
    legitimately fall back to active listings.
    """

    def __init__(self, client_id: str, client_secret: str, marketplace_id: str, category_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.category_id = category_id

    def _token(self, scope: str) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("eBay credentials are not configured")

        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        response = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": scope},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def price(self, asset: Asset) -> PriceResult:
        sold_error: Optional[Exception] = None

        try:
            return self._price_sold(asset)
        except Exception as exc:
            sold_error = exc

        try:
            result = self._price_active(asset)
            result.notes = (
                f"{result.notes}; sold-history unavailable ({self._short_error(sold_error)})"
            )
            return result
        except Exception as active_error:
            raise RuntimeError(
                f"eBay sold lookup failed ({self._short_error(sold_error)}); "
                f"eBay active-listing lookup failed ({self._short_error(active_error)})"
            ) from active_error

    def _price_sold(self, asset: Asset) -> PriceResult:
        query = asset.ebay_query or asset.name
        now = datetime.now(timezone.utc)
        start_30 = now - timedelta(days=30)

        filter_value = (
            f"lastSoldDate:[{start_30.strftime('%Y-%m-%dT%H:%M:%SZ')}.."
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}]"
        )

        response = requests.get(
            SOLD_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {self._token(SOLD_SCOPE)}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            params={
                "q": query,
                "category_ids": self.category_id,
                "filter": filter_value,
                "limit": 200,
            },
            timeout=30,
        )
        response.raise_for_status()

        payload = response.json()
        items = payload.get("itemSales") or payload.get("itemSummaries") or []
        comps = []

        for item in items:
            price_node = item.get("price") or item.get("lastSoldPrice") or {}
            value = price_node.get("value") if isinstance(price_node, dict) else None
            currency = price_node.get("currency") if isinstance(price_node, dict) else None
            if value is None or (currency and currency != "USD"):
                continue

            title = str(item.get("title") or "")
            if not self._title_match(query, title):
                continue

            sold_date = item.get("lastSoldDate") or item.get("itemEndDate")
            comps.append((float(value), sold_date))

        if not comps:
            raise LookupError(f"No matching eBay sold comps found for {query!r}")

        values_30 = [value for value, _ in comps]
        cutoff_7 = now - timedelta(days=7)
        values_7 = [
            value for value, raw_date in comps
            if raw_date and _parse_date(raw_date) >= cutoff_7
        ]

        dated = [(value, _parse_date(raw_date)) for value, raw_date in comps if raw_date]
        last_sold = max(dated, key=lambda x: x[1])[0] if dated else values_30[0]
        med = median(values_30)

        return PriceResult(
            source="EBAY_SOLD",
            estimated_value=med,
            last_sold=last_sold,
            average_7d=mean(values_7) if values_7 else None,
            median_30d=med,
            low_30d=min(values_30),
            high_30d=max(values_30),
            sales_count_30d=len(values_30),
            notes=f"Median of {len(values_30)} matching eBay sold comps / 30d",
        )

    def _price_active(self, asset: Asset) -> PriceResult:
        query = asset.ebay_query or asset.name

        response = requests.get(
            BROWSE_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {self._token(BASIC_SCOPE)}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            params={
                "q": query,
                "category_ids": self.category_id,
                "filter": "buyingOptions:{FIXED_PRICE}",
                "limit": 50,
            },
            timeout=30,
        )
        response.raise_for_status()

        items = response.json().get("itemSummaries") or []
        comps = []

        for item in items:
            title = str(item.get("title") or "")
            if not self._title_match(query, title):
                continue

            price_node = item.get("price") or {}
            value = price_node.get("value")
            currency = price_node.get("currency")
            if value is None or currency != "USD":
                continue

            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            if numeric > 0:
                comps.append(numeric)

        if not comps:
            raise LookupError(f"No matching fixed-price eBay listings found for {query!r}")

        filtered = self._trim_outliers(comps)
        med = median(filtered)

        return PriceResult(
            source="EBAY_ACTIVE",
            estimated_value=med,
            low_30d=min(filtered),
            high_30d=max(filtered),
            notes=(
                f"Median asking price of {len(filtered)} matching fixed-price "
                "eBay US listings (not sold comps)"
            ),
        )

    @staticmethod
    def _title_match(query: str, title: str) -> bool:
        query_norm = _normalize(query)
        title_norm = _normalize(title)

        if any(bad in title_norm for bad in ("proxy", "custom", "orica", "digital")):
            return False

        if " lot " in f" {title_norm} " and " lot " not in f" {query_norm} ":
            return False

        query_grade = re.search(r"\b(psa|bgs|cgc)\s*(\d+(?:\.\d+)?)\b", query_norm)
        title_grade = re.search(r"\b(psa|bgs|cgc)\s*(\d+(?:\.\d+)?)\b", title_norm)

        if query_grade:
            if not title_grade or query_grade.groups() != title_grade.groups():
                return False
        elif title_grade:
            return False

        stop = {
            "riftbound", "pokemon", "card", "cards", "sealed",
            "official", "tcg", "the", "and", "for",
        }
        wanted = {
            token for token in query_norm.split()
            if len(token) >= 3 and token not in stop
        }
        found = set(title_norm.split())

        if not wanted:
            return True

        overlap = len(wanted & found)
        required = max(2, (len(wanted) + 1) // 2)
        return overlap >= min(required, len(wanted))

    @staticmethod
    def _trim_outliers(values: list[float]) -> list[float]:
        if len(values) < 5:
            return values

        ordered = sorted(values)
        midpoint = len(ordered) // 2
        lower = ordered[:midpoint]
        upper = ordered[midpoint + (len(ordered) % 2):]
        q1 = median(lower)
        q3 = median(upper)
        iqr = q3 - q1

        if iqr <= 0:
            return ordered

        low = max(0.01, q1 - 1.5 * iqr)
        high = q3 + 1.5 * iqr
        trimmed = [value for value in ordered if low <= value <= high]
        return trimmed or ordered

    @staticmethod
    def _short_error(error: Optional[Exception]) -> str:
        if error is None:
            return "unknown error"
        text = str(error).strip() or error.__class__.__name__
        return text[:180]


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return " ".join(text.split())


def _parse_date(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
