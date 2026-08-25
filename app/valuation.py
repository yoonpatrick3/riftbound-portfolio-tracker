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
            try:
                return self.tcggo.price(asset)
            except Exception as tcggo_error:
                try:
                    result = self.ebay.price(asset)
                    result.notes = (
                        f"{result.notes}; TCGGO fallback reason: "
                        f"{self._short_error(tcggo_error)}"
                    )
                    return result
                except Exception as ebay_error:
                    raise RuntimeError(
                        f"TCGGO lookup failed ({self._short_error(tcggo_error)}); "
                        f"eBay fallback failed ({self._short_error(ebay_error)})"
                    ) from ebay_error

        if source == "EBAY_SOLD":
            # Explicit eBay rows go straight to eBay. The provider prefers sold
            # history when available and otherwise uses active fixed-price comps.
            return self.ebay.price(asset)

        raise ValueError(
            f"Unsupported Pricing Source {source!r} for {asset.name}. "
            "Supported sources: TCGGO and EBAY_SOLD."
        )

    @staticmethod
    def _short_error(error: Exception) -> str:
        text = str(error).strip() or error.__class__.__name__
        return text[:220]
