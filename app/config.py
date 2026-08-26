import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    google_sheet_id: str
    google_service_account: dict
    tcggo_api_key: str
    tcggo_api_host: str
    tcggo_api_base: str
    ebay_client_id: str
    ebay_client_secret: str
    ebay_marketplace_id: str
    ebay_category_id: str
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str
    mercari_lookback_days: int


def load_config() -> Config:
    raw_sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_sa:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is required")

    return Config(
        google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
        google_service_account=json.loads(raw_sa),
        tcggo_api_key=os.environ.get("TCGGO_API_KEY", ""),
        tcggo_api_host=os.environ.get(
            "TCGGO_API_HOST", "cardmarket-api-tcg.p.rapidapi.com"
        ),
        tcggo_api_base=os.environ.get(
            "TCGGO_API_BASE", "https://cardmarket-api-tcg.p.rapidapi.com"
        ),
        ebay_client_id=os.environ.get("EBAY_CLIENT_ID", ""),
        ebay_client_secret=os.environ.get("EBAY_CLIENT_SECRET", ""),
        ebay_marketplace_id=os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US"),
        ebay_category_id=os.environ.get("EBAY_CATEGORY_ID", "183454"),
        gmail_client_id=os.environ.get("GMAIL_CLIENT_ID", ""),
        gmail_client_secret=os.environ.get("GMAIL_CLIENT_SECRET", ""),
        gmail_refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN", ""),
        mercari_lookback_days=int(os.environ.get("MERCARI_LOOKBACK_DAYS", "365")),
    )
