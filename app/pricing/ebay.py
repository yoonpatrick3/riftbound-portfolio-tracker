from datetime import datetime, timedelta, timezone
from statistics import mean, median
import base64
import requests

from ..models import Asset, PriceResult

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SOLD_SEARCH_URL = "https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search"
SCOPE = "https://api.ebay.com/oauth/api_scope/commerce.marketplace.insights.readonly"

class EbaySoldPricingProvider:
    """
    Uses eBay Marketplace Insights sold-item history.

    Important: eBay currently restricts this API and says it is not open to
    new users. The adapter is intentionally isolated so another sold-comps
    provider can replace it later without changing the sheet or runner.
    """

    def __init__(self, client_id: str, client_secret: str, marketplace_id: str, category_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.category_id = category_id

    def _token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("eBay credentials are not configured")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": SCOPE},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]

    def price(self, asset: Asset) -> PriceResult:
        query = asset.ebay_query or asset.name
        now = datetime.now(timezone.utc)
        start_30 = now - timedelta(days=30)

        filter_value = (
            f"lastSoldDate:[{start_30.strftime('%Y-%m-%dT%H:%M:%SZ')}.."
            f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}]"
        )
        r = requests.get(
            SOLD_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {self._token()}",
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
        r.raise_for_status()
        payload = r.json()
        items = payload.get("itemSales") or payload.get("itemSummaries") or []

        comps = []
        for item in items:
            p = item.get("price") or item.get("lastSoldPrice") or {}
            value = p.get("value") if isinstance(p, dict) else None
            if value is None:
                continue
            sold_date = item.get("lastSoldDate") or item.get("itemEndDate")
            comps.append((float(value), sold_date))

        if not comps:
            raise LookupError(f"No eBay sold comps found for {query!r}")

        values_30 = [x[0] for x in comps]
        cutoff_7 = now - timedelta(days=7)
        values_7 = [
            value for value, raw_date in comps
            if raw_date and _parse_date(raw_date) >= cutoff_7
        ]

        # Last sold = newest dated comp; if dates are absent, first API result.
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
            notes=f"Median of {len(values_30)} eBay sold comps / 30d",
        )

def _parse_date(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
