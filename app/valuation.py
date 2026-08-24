from .models import Asset, PriceResult
from .pricing.ebay import EbaySoldPricingProvider
from .pricing.tcggo import TcggoPricingProvider


class PricingRouter:
    def __init__(self, cfg):
        self.tcggo = TcggoPricingProvider(
            cfg.tcggo_api_key,
            cfg.tcggo_api_host,
            cfg.tcggo_api_base,
        )
        self.ebay = EbaySoldPricingProvider(
            cfg.ebay_client_id,
            cfg.ebay_client_secret,
            cfg.ebay_marketplace_id,
            cfg.ebay_category_id,
        )

    def price(self, asset: Asset) -> PriceResult:
        source = (asset.pricing_source or "TCGGO").upper()

        if source == "TCGGO":
            return self.tcggo.price(asset)

        if source == "EBAY_SOLD":
            # Try TCGGO first. It already exposes eBay graded medians for many
            # catalogued slabs, avoiding direct eBay credentials when possible.
            try:
                result = self.tcggo.price(asset)
                result.notes = f"{result.notes}; EBAY_SOLD row satisfied by TCGGO"
                return result
            except Exception as tcggo_error:
                try:
                    return self.ebay.price(asset)
                except Exception as ebay_error:
                    raise RuntimeError(
                        f"TCGGO fallback failed ({tcggo_error}); "
                        f"direct eBay sold lookup failed ({ebay_error})"
                    ) from ebay_error

        raise ValueError(
            f"Unsupported Pricing Source {source!r} for {asset.name}. "
            "V2 supports TCGGO and EBAY_SOLD only."
        )
