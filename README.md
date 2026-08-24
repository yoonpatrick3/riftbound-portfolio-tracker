# TCG Portfolio Tracker V2

Automatic weekly portfolio pricing from your Google Sheet.

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

## First run

1. Put these files in your GitHub repository.
2. Add the three required repository secrets.
3. Make sure your Google service account has Editor access to the spreadsheet.
4. In GitHub, go to **Actions → Weekly TCG Portfolio Snapshot → Run workflow**.
5. Check `Tracking Assets` and `Price History` after it completes.

## Sheet ownership

You edit columns A:I (except Pricing Key can auto-resolve). `Manual Value` is ignored and can remain blank. The script owns K:O.

When you sell an asset, set `Active = No`; do not delete the row.
