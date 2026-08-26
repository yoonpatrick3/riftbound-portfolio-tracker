# TCG Portfolio Tracker V2

Automatic weekly portfolio pricing from your Google Sheet.

The weekly workflow also imports Mercari sales from Gmail into a deduplicated
`Sales` worksheet. It records transaction ID, item, sale date, gross price,
fees, net earnings, buyer, and lifecycle status through `PAID`.

## Pricing sources

- `TCGGO` — primary source for Riftbound, Pokémon, sealed products, raw cards, and supported graded prices.
- `EBAY_SOLD` — first tries TCGGO (which includes eBay graded medians for catalogued slabs), then falls back to eBay Marketplace Insights if configured.
- No manual pricing provider is used in V2.

## Pricing Key format

Examples:

```text
riftbound:card:33384
riftbound:product:35013
pokemon:card:19338
riftbound:search:Lee Sin Blind Monk OGN-304/298
```

A `search` key is automatically resolved to a numeric card/product ID on the first successful run and written back into column H.

## GitHub secrets

Required:

```text
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON
TCGGO_API_KEY
```

Optional eBay fallback:

```text
EBAY_CLIENT_ID
EBAY_CLIENT_SECRET
```

Optional Mercari automation:

```text
GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN
```

Create a Google OAuth client with the Gmail read-only scope, generate a refresh
token for the Gmail account that receives Mercari notifications, and store the
three values as GitHub repository secrets. If they are absent, pricing still
runs and Mercari synchronization is skipped.

Mercari bundle emails expose the bundle total but not the individual cards.
Those rows are flagged in `Notes` so their contents can be reconciled from the
Mercari sales report without inventing inventory changes.

## First run

1. Put these files in your GitHub repository.
2. Add the three required repository secrets.
3. Make sure your Google service account has Editor access to the spreadsheet.
4. In GitHub, go to **Actions → Weekly TCG Portfolio Snapshot → Run workflow**.
5. Check `Tracking Assets` and `Price History` after it completes.

## Sheet ownership

You edit columns A:I (except Pricing Key can auto-resolve). `Manual Value` is ignored and can remain blank. The script owns K:O.

When you sell an asset, set `Active = No`; do not delete the row.
