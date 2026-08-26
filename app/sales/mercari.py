from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re
from typing import Iterable, Optional

MERCARI_QUERY = (
    'from:no-reply@alerts.us.mercari.com '
    '(subject:"You\'ve made a sale" OR subject:"Your item is on its way" '
    'OR subject:"You\'ve been rated" OR subject:"You\'ve been paid")'
)


@dataclass
class MercariSale:
    transaction_id: str
    item: str
    sold_at: str
    gross_price: Optional[float] = None
    selling_fee: Optional[float] = None
    shipping_fee: Optional[float] = None
    earnings: Optional[float] = None
    buyer: str = ""
    status: str = "SOLD"
    paid_at: str = ""
    gmail_message_id: str = ""
    notes: str = ""


class GmailMercariClient:
    """Read Mercari transaction notifications with a Gmail OAuth refresh token."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        import requests

        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def fetch_sales(self, lookback_days: int = 365) -> list[MercariSale]:
        if not self.enabled:
            return []

        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        query = f"{MERCARI_QUERY} newer_than:{max(1, lookback_days)}d"
        message_ids: list[str] = []
        page_token = None

        while True:
            params = {"q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            response = self.session.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            message_ids.extend(item["id"] for item in payload.get("messages", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        messages = []
        for message_id in message_ids:
            response = self.session.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
                timeout=30,
            )
            response.raise_for_status()
            messages.append(response.json())

        return merge_mercari_messages(messages)

    def _access_token(self) -> str:
        response = self.session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]


def merge_mercari_messages(messages: Iterable[dict]) -> list[MercariSale]:
    """Parse sale notices and fold later lifecycle emails into each sale."""
    parsed = []
    for message in messages:
        payload = message.get("payload") or {}
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in payload.get("headers", [])
        }
        subject = html.unescape(headers.get("subject", "")).strip()
        body = _plain_text(payload)
        occurred_at = _message_time(message, headers.get("date", ""))
        parsed.append((occurred_at, subject, body, str(message.get("id", ""))))

    parsed.sort(key=lambda value: value[0])
    sales: list[MercariSale] = []

    for occurred_at, subject, body, gmail_id in parsed:
        event, item = _subject_event(subject)
        if not event or not item:
            continue

        if event == "SOLD":
            sale = parse_sale_email(subject, body, occurred_at, gmail_id)
            existing = next(
                (candidate for candidate in sales if candidate.transaction_id == sale.transaction_id),
                None,
            )
            if existing:
                existing.__dict__.update(sale.__dict__)
            else:
                sales.append(sale)
            continue

        sale = _latest_matching_sale(sales, item, occurred_at)
        if not sale:
            # Lifecycle-only messages do not reliably contain price or transaction
            # id. Keep them out until their original sale notice is available.
            continue
        sale.status = _later_status(sale.status, event)
        if event == "PAID":
            sale.paid_at = occurred_at.isoformat(timespec="seconds")

    return sales


def parse_sale_email(
    subject: str,
    body: str,
    occurred_at: datetime,
    gmail_message_id: str,
) -> MercariSale:
    _, item = _subject_event(subject)
    transaction = re.search(r"\bID:\s*([a-z]\d{8,})\b", body, re.IGNORECASE)
    if not transaction:
        raise ValueError(f"Mercari sale email has no transaction ID: {subject}")

    gross, selling_fee, shipping_fee = _price_breakdown(body)
    earnings_match = re.search(r"Earnings\s*\$([\d,]+(?:\.\d{2})?)", body, re.IGNORECASE)
    buyer_match = re.search(
        r"Shipping to\s+([^\n]+)", _clean_lines(body), re.IGNORECASE
    )
    notes = ""
    if item.lower().startswith("bundle for "):
        notes = "Bundle contents are not included in Mercari notification emails"

    return MercariSale(
        transaction_id=transaction.group(1),
        item=item,
        sold_at=occurred_at.isoformat(timespec="seconds"),
        gross_price=gross,
        selling_fee=selling_fee,
        shipping_fee=shipping_fee,
        earnings=_money(earnings_match.group(1)) if earnings_match else None,
        buyer=buyer_match.group(1).strip() if buyer_match else "",
        status="SOLD",
        gmail_message_id=gmail_message_id,
        notes=notes,
    )


def _price_breakdown(body: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    start = body.lower().find("price")
    end = body.lower().find("earnings", start + 1)
    if start < 0 or end < 0:
        return None, None, None
    amounts = re.findall(r"(-?)\$([\d,]+(?:\.\d{2})?)", body[start:end])
    values = [(-1 if sign else 1) * _money(value) for sign, value in amounts]
    gross = abs(values[0]) if values else None
    selling = abs(values[1]) if len(values) > 1 else None
    shipping = abs(values[2]) if len(values) > 2 else 0.0
    return gross, selling, shipping


def _subject_event(subject: str) -> tuple[str, str]:
    patterns = [
        ("SOLD", r"^You['’]ve made a sale:\s*(.+)$"),
        ("SHIPPED", r"^Your item is on its way:\s*(.+)$"),
        ("RATED", r"^You['’]ve been rated:\s*(.+)$"),
        ("PAID", r"^You['’]ve been paid:\s*(.+)$"),
    ]
    for event, pattern in patterns:
        match = re.match(pattern, subject, re.IGNORECASE)
        if match:
            return event, match.group(1).strip()
    return "", ""


def _latest_matching_sale(
    sales: list[MercariSale], item: str, occurred_at: datetime
) -> Optional[MercariSale]:
    normalized = _normalize_item(item)
    candidates = [
        sale
        for sale in sales
        if _normalize_item(sale.item) == normalized
        and datetime.fromisoformat(sale.sold_at) <= occurred_at
    ]
    return max(candidates, key=lambda sale: sale.sold_at, default=None)


def _later_status(current: str, proposed: str) -> str:
    order = {"SOLD": 0, "SHIPPED": 1, "RATED": 2, "PAID": 3}
    return proposed if order.get(proposed, -1) > order.get(current, -1) else current


def _plain_text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain":
        return _decode_body((payload.get("body") or {}).get("data", ""))
    for part in payload.get("parts") or []:
        text = _plain_text(part)
        if text:
            return text
    return ""


def _decode_body(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _message_time(message: dict, date_header: str) -> datetime:
    if date_header:
        try:
            value = parsedate_to_datetime(date_header)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    millis = int(message.get("internalDate") or 0)
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _clean_lines(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _normalize_item(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _money(value: str) -> float:
    return float(value.replace(",", ""))
