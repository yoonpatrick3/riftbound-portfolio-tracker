import base64
from datetime import datetime, timezone

from app.sales.mercari import merge_mercari_messages, parse_sale_email


def _message(subject, body, timestamp, message_id):
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "internalDate": str(int(timestamp.timestamp() * 1000)),
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": encoded}}
            ],
        },
    }


def test_parse_sale_financials():
    body = """
Item details
Riftbound Akali Silent Alt Art VEN 038a/166
ID: m52350310847
Price
Selling fee
Shipping fee
$16.00
-$1.60
-$0.49
Earnings
$13.91
Shipping to
kristineca28
Jersey City, NJ
"""
    sale = parse_sale_email(
        "You've made a sale: Riftbound Akali Silent Alt Art VEN 038a/166",
        body,
        datetime(2026, 8, 25, tzinfo=timezone.utc),
        "gmail-1",
    )
    assert sale.transaction_id == "m52350310847"
    assert sale.gross_price == 16.0
    assert sale.selling_fee == 1.6
    assert sale.shipping_fee == 0.49
    assert sale.earnings == 13.91
    assert sale.buyer == "kristineca28"


def test_lifecycle_messages_merge_without_duplicates():
    sold = datetime(2026, 8, 23, tzinfo=timezone.utc)
    body = """
ID: m24029615337
Price
Selling fee
Shipping fee
$15.50
-$1.55
-$0.49
Earnings
$13.46
Shipping to
buyer123
"""
    messages = [
        _message("You've made a sale: Riftbound Sabotage OGN 156/298", body, sold, "1"),
        _message("Your item is on its way: Riftbound Sabotage OGN 156/298", "", sold.replace(day=24), "2"),
        _message("You've been paid: Riftbound Sabotage OGN 156/298", "", sold.replace(day=26), "3"),
    ]
    sales = merge_mercari_messages(messages)
    assert len(sales) == 1
    assert sales[0].status == "PAID"
    assert sales[0].paid_at.startswith("2026-08-26")


def test_bundle_is_flagged_for_manual_contents():
    body = "ID: b59314282339\nPrice\nSelling fee\nShipping fee\n$30.00\n-$3.00\n-$5.66\nEarnings\n$21.34"
    sale = parse_sale_email(
        "You've made a sale: Bundle for Desiree Faith",
        body,
        datetime(2026, 8, 24, tzinfo=timezone.utc),
        "gmail-2",
    )
    assert "Bundle contents" in sale.notes

