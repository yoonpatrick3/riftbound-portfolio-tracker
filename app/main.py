import logging
from dotenv import load_dotenv

from .config import load_config
from .sheets import SheetStore
from .valuation import PricingRouter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("riftbound-tracker")


def run():
    load_dotenv()
    cfg = load_config()
    store = SheetStore(cfg.google_sheet_id, cfg.google_service_account)
    router = PricingRouter(cfg)

    assets = store.load_assets()
    log.info("Pricing %d active assets", len(assets))

    failures = []
    successes = 0

    for asset in assets:
        try:
            result = router.price(asset)
            store.update_asset_price(asset, result)
            store.append_snapshot(asset, result)
            successes += 1
            log.info("%s -> $%.2f (%s)", asset.name, result.estimated_value, result.source)
        except Exception as exc:
            failures.append((asset.name, str(exc)))
            log.exception("Failed pricing %s", asset.name)
            try:
                store.mark_asset_unpriced(asset, exc)
            except Exception:
                log.exception("Failed marking %s as unpriced", asset.name)

    log.info("Finished: %d succeeded, %d failed", successes, len(failures))

    if failures:
        summary = "; ".join(f"{name}: {error}" for name, error in failures)
        log.warning("%d assets were left unpriced this run: %s", len(failures), summary)


if __name__ == "__main__":
    run()
