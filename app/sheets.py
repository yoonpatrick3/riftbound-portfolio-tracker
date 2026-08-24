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
                cost_basis=float(row.get("Cost Basis") or 0),
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

    def append_snapshot(self, asset: Asset, result: PriceResult) -> None:
        now = datetime.now(timezone.utc)
        unit = round(result.estimated_value, 2)

        self.history_ws.append_row([
            now.isoformat(timespec="seconds"),
            now.date().isoformat(),
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
        ], value_input_option="USER_ENTERED")
