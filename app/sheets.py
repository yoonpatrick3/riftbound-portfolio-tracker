from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from .models import Asset, PriceResult

ASSET_SHEET = "Tracking Assets"
HISTORY_SHEET = "Price History"

# Manual Value is retained in the sheet only for backwards compatibility with
# the existing workbook. V2 never reads or prices from it.
ASSET_HEADERS = [
    "Asset ID", "Asset", "Type", "Quantity", "Cost Basis", "Active",
    "Pricing Source", "Pricing Key", "eBay Query", "Manual Value",
    "Current Unit Value", "Current Total Value", "Unrealized P/L",
    "Last Updated", "Notes"
]

HISTORY_HEADERS = [
    "Timestamp", "Week", "Asset ID", "Asset", "Source", "Unit Value",
    "Quantity", "Total Value", "Last Sold", "Avg 7d", "Median 30d",
    "Low 30d", "High 30d", "Sales Count 30d", "Notes"
]


class SheetStore:
    def __init__(self, spreadsheet_id: str, service_account_info: dict):
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        self.book = gspread.authorize(creds).open_by_key(spreadsheet_id)
        self.assets_ws = self.book.worksheet(ASSET_SHEET)
        self.history_ws = self.book.worksheet(HISTORY_SHEET)
        self._history_row_by_key: dict[tuple[str, str], int] | None = None
        self._history_last_row = 1

    def load_assets(self) -> list[Asset]:
        rows = self.assets_ws.get_all_records(expected_headers=ASSET_HEADERS)
        assets = []

        for idx, row in enumerate(rows, start=2):
            active_raw = str(row.get("Active", "")).strip().lower()
            active = active_raw in {"yes", "y", "true", "1"}
            if not active:
                continue

            asset_id = str(row.get("Asset ID", "")).strip()
            name = str(row.get("Asset", "")).strip()
            if not asset_id or not name:
                continue

            pricing_source = str(row.get("Pricing Source", "")).strip().upper()
            if not pricing_source:
                pricing_source = "TCGGO"

            assets.append(Asset(
                row_number=idx,
                asset_id=asset_id,
                name=name,
                asset_type=str(row.get("Type", "")).strip(),
                quantity=max(1, int(float(row.get("Quantity") or 1))),
                cost_basis=_money(row.get("Cost Basis")),
                active=active,
                pricing_source=pricing_source,
                pricing_key=str(row.get("Pricing Key", "")).strip(),
                ebay_query=str(row.get("eBay Query", "")).strip(),
            ))

        return assets

    def update_asset_price(self, asset: Asset, result: PriceResult) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        unit = round(result.estimated_value, 2)
        total = round(unit * asset.quantity, 2)
        pnl = round(total - asset.cost_basis, 2)

        # If a search key resolved to an exact numeric TCGGO id, persist it.
        if result.resolved_pricing_key and result.resolved_pricing_key != asset.pricing_key:
            self.assets_ws.update(
                range_name=f"H{asset.row_number}",
                values=[[result.resolved_pricing_key]],
                value_input_option="USER_ENTERED",
            )

        # K:O are script-owned output columns.
        self.assets_ws.update(
            range_name=f"K{asset.row_number}:O{asset.row_number}",
            values=[[unit, total, pnl, now, result.notes]],
            value_input_option="USER_ENTERED",
        )

    def mark_asset_unpriced(self, asset: Asset, error: Exception) -> None:
        """Clear stale valuation output when an asset cannot be priced this run."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        message = str(error).strip() or error.__class__.__name__
        if len(message) > 450:
            message = message[:447] + "..."

        self.assets_ws.update(
            range_name=f"K{asset.row_number}:O{asset.row_number}",
            values=[["", "", "", now, f"UNPRICED — {message}"]],
            value_input_option="USER_ENTERED",
        )

    def append_snapshot(self, asset: Asset, result: PriceResult) -> None:
        """Write one history row per asset per UTC day.

        Re-running the workflow on the same day updates that asset's existing
        row instead of appending another testing duplicate.
        """
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        unit = round(result.estimated_value, 2)
        row_values = [
            now.isoformat(timespec="seconds"),
            day,
            asset.asset_id,
            asset.name,
            result.source,
            unit,
            asset.quantity,
            round(unit * asset.quantity, 2),
            result.last_sold,
            result.average_7d,
            result.median_30d,
            result.low_30d,
            result.high_30d,
            result.sales_count_30d,
            result.notes,
        ]

        self._ensure_history_index()
        key = (day, asset.asset_id)
        existing_row = self._history_row_by_key.get(key)

        if existing_row:
            self.history_ws.update(
                range_name=f"A{existing_row}:O{existing_row}",
                values=[row_values],
                value_input_option="USER_ENTERED",
            )
            return

        self.history_ws.append_row(row_values, value_input_option="USER_ENTERED")
        self._history_last_row += 1
        self._history_row_by_key[key] = self._history_last_row

    def _ensure_history_index(self) -> None:
        if self._history_row_by_key is not None:
            return

        values = self.history_ws.get_all_values()
        self._history_last_row = max(1, len(values))
        self._history_row_by_key = {}

        # Column B = Week/day; Column C = Asset ID. If historical testing has
        # already produced duplicates, use the latest one and stop creating more.
        for row_number, row in enumerate(values[1:], start=2):
            if len(row) < 3:
                continue
            day = str(row[1]).strip()
            asset_id = str(row[2]).strip()
            if day and asset_id:
                self._history_row_by_key[(day, asset_id)] = row_number


def _money(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().replace("$", "").replace(",", "") or 0)
