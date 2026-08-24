from dataclasses import dataclass
from typing import Optional


@dataclass
class Asset:
    row_number: int
    asset_id: str
    name: str
    asset_type: str
    quantity: int
    cost_basis: float
    active: bool
    pricing_source: str
    pricing_key: str
    ebay_query: str


@dataclass
class PriceResult:
    source: str
    estimated_value: float
    last_sold: Optional[float] = None
    average_7d: Optional[float] = None
    median_30d: Optional[float] = None
    low_30d: Optional[float] = None
    high_30d: Optional[float] = None
    sales_count_30d: Optional[int] = None
    notes: str = ""
    resolved_pricing_key: Optional[str] = None
