import re
from dataclasses import dataclass
from typing import Any, Optional

import requests

from ..models import Asset, PriceResult


@dataclass(frozen=True)
class PricingKey:
    game: str
    kind: str
    value: str


class TcggoPricingProvider:
    """Automatic pricing via TCGGO's multi-TCG RapidAPI API.

    Supported sheet keys:
      riftbound:card:33384
      riftbound:product:35013
      pokemon:card:19338
      riftbound:search:Lee Sin Blind Monk OGN-304/298

    Search keys are resolved once and persisted back to the sheet as an exact
    numeric card/product key by SheetStore.update_asset_price().
    """

    def __init__(self, api_key: str, host: str, base_url: str):
        self.api_key = api_key
        self.host = host
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("TCGGO_API_KEY is not configured")
        return {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": self.host,
        }

    def price(self, asset: Asset) -> PriceResult:
        key = self._parse_or_infer_key(asset)
        item, resolved_key = self._fetch_item(asset, key)
        estimate, note = self._select_value(asset, item)

        return PriceResult(
            source="TCGGO",
            estimated_value=estimate,
            last_sold=self._extract_last_sold(asset, item),
            average_7d=self._extract_average(item, 7),
            median_30d=self._extract_median_30d(asset, item),
            notes=note,
            resolved_pricing_key=resolved_key,
        )

    def _parse_or_infer_key(self, asset: Asset) -> PricingKey:
        raw = (asset.pricing_key or "").strip()
        if raw:
            parts = raw.split(":", 2)
            if len(parts) != 3:
                raise ValueError(
                    f"{asset.name}: invalid Pricing Key {raw!r}; expected game:card|product|search:value"
                )
            game, kind, value = parts
            game = game.lower().strip()
            kind = kind.lower().strip()
            if game not in {"riftbound", "pokemon", "one-piece", "lorcana"}:
                raise ValueError(f"{asset.name}: unsupported TCGGO game {game!r}")
            if kind not in {"card", "product", "search"}:
                raise ValueError(f"{asset.name}: unsupported Pricing Key kind {kind!r}")
            return PricingKey(game, kind, value.strip())

        # Missing key: search automatically. Sealed assets search products;
        # everything else searches cards.
        query = asset.ebay_query or asset.name
        return PricingKey("riftbound", "search", query)

    def _fetch_item(self, asset: Asset, key: PricingKey) -> tuple[dict, Optional[str]]:
        if key.kind in {"card", "product"}:
            endpoint = "cards" if key.kind == "card" else "products"
            data = self._get_json(f"/{key.game}/{endpoint}/{key.value}")
            return self._unwrap_single(data), None

        # Search keys decide cards vs products using the asset type/name.
        is_product = self._is_product(asset)
        endpoint = "products" if is_product else "cards"
        payload = self._get_json(
            f"/{key.game}/{endpoint}/search",
            params={"search": key.value, "sort": "relevance"},
        )
        candidates = self._unwrap_list(payload)
        if not candidates:
            raise LookupError(f"{asset.name}: TCGGO search returned no matches for {key.value!r}")

        match = self._best_match(asset, key.value, candidates)
        item_id = match.get("id")
        if item_id is None:
            raise LookupError(f"{asset.name}: TCGGO match had no numeric id")

        # Fetch full detail because search results may have abbreviated prices.
        detail = self._get_json(f"/{key.game}/{endpoint}/{item_id}")
        resolved = f"{key.game}:{'product' if is_product else 'card'}:{item_id}"
        return self._unwrap_single(detail), resolved

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        response = requests.get(
            f"{self.base_url}{path}",
            headers=self.headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _unwrap_single(payload: Any) -> dict:
        if isinstance(payload, dict):
            for key in ("data", "card", "product", "result"):
                inner = payload.get(key)
                if isinstance(inner, dict):
                    return inner
            return payload
        raise ValueError("Unexpected TCGGO detail response")

    @staticmethod
    def _unwrap_list(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "cards", "products", "results", "items"):
                inner = payload.get(key)
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
        return []

    def _best_match(self, asset: Asset, query: str, candidates: list[dict]) -> dict:
        wanted_number = self._collector_number(query)
        wanted_name = self._normalize_name(asset.name)

        scored = []
        for candidate in candidates:
            score = 0
            card_number = str(candidate.get("card_number") or candidate.get("number") or "")
            name = self._normalize_name(str(candidate.get("name") or ""))

            if wanted_number and wanted_number.lower() == card_number.lower():
                score += 100
            elif wanted_number and wanted_number.lower() in card_number.lower():
                score += 80

            # Useful for things like "ON Jinx" where API name is "Jinx, Loose Cannon".
            for token in wanted_name.split():
                if len(token) >= 3 and token in name:
                    score += 5

            # Prefer candidates carrying a price payload.
            if candidate.get("prices"):
                score += 2

            scored.append((score, candidate))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]

        # A search result with no meaningful overlap is too risky to auto-price.
        if best_score <= 0:
            raise LookupError(
                f"{asset.name}: TCGGO search was ambiguous; add an exact Pricing Key"
            )
        return best

    def _select_value(self, asset: Asset, item: dict) -> tuple[float, str]:
        prices = item.get("prices") or {}
        grade = self._grade(asset.asset_type)

        if grade:
            graded_value = self._graded_value(prices, grade)
            if graded_value is not None:
                return graded_value, f"TCGGO eBay graded median ({grade[0].upper()} {grade[1]})"

            raise ValueError(
                f"{asset.name}: TCGGO returned no graded price for {grade[0].upper()} {grade[1]}"
            )

        tcg = prices.get("tcg_player") or prices.get("tcgplayer") or {}
        cardmarket = prices.get("cardmarket") or {}

        choices = [
            (self._num(tcg.get("market_price")), "TCGGO / TCGplayer market"),
            (self._num(tcg.get("market")), "TCGGO / TCGplayer market"),
            (self._num(cardmarket.get("lowest_near_mint")), "TCGGO / Cardmarket NM"),
            (self._num(cardmarket.get("trend_price")), "TCGGO / Cardmarket trend"),
            (self._num(cardmarket.get("trend")), "TCGGO / Cardmarket trend"),
            (self._num(cardmarket.get("avg_7d")), "TCGGO / Cardmarket 7d average"),
            (self._num(cardmarket.get("avg_30d")), "TCGGO / Cardmarket 30d average"),
        ]
        for value, note in choices:
            if value is not None and value > 0:
                return value, note

        # Some product responses expose a simpler top-level price object.
        for key in ("market_price", "price", "lowest_near_mint"):
            value = self._num(prices.get(key))
            if value is not None and value > 0:
                return value, f"TCGGO product price ({key})"

        raise ValueError(f"{asset.name}: TCGGO returned no usable automatic price")

    def _graded_value(self, prices: dict, grade: tuple[str, str]) -> Optional[float]:
        company, number = grade

        # Documented TCGGO shape:
        # prices.ebay.graded.psa.10.median_price
        ebay = prices.get("ebay") or {}
        graded = ebay.get("graded") or prices.get("graded") or prices.get("graded_prices") or {}
        company_node = graded.get(company) or graded.get(company.upper()) or {}

        if isinstance(company_node, dict):
            node = company_node.get(number) or company_node.get(str(number))
            if isinstance(node, dict):
                for key in ("median_price", "median", "market_price", "price"):
                    value = self._num(node.get(key))
                    if value is not None:
                        return value
            return self._num(node)

        return None

    def _extract_last_sold(self, asset: Asset, item: dict) -> Optional[float]:
        grade = self._grade(asset.asset_type)
        if not grade:
            return None
        prices = item.get("prices") or {}
        ebay = prices.get("ebay") or {}
        graded = ebay.get("graded") or {}
        node = (graded.get(grade[0]) or {}).get(grade[1]) or {}
        if isinstance(node, dict):
            return self._num(node.get("last_sold") or node.get("last_sold_price"))
        return None

    def _extract_average(self, item: dict, days: int) -> Optional[float]:
        prices = item.get("prices") or {}
        cm = prices.get("cardmarket") or {}
        return self._num(cm.get(f"avg_{days}d"))

    def _extract_median_30d(self, asset: Asset, item: dict) -> Optional[float]:
        grade = self._grade(asset.asset_type)
        if grade:
            return self._graded_value(item.get("prices") or {}, grade)
        prices = item.get("prices") or {}
        cm = prices.get("cardmarket") or {}
        return self._num(cm.get("avg_30d"))

    @staticmethod
    def _is_product(asset: Asset) -> bool:
        text = f"{asset.asset_type} {asset.name}".lower()
        return any(word in text for word in ("sealed", "box", "bundle", "display", "booster"))

    @staticmethod
    def _grade(asset_type: str) -> Optional[tuple[str, str]]:
        match = re.search(r"\b(PSA|BGS|CGC)\s*([0-9]+(?:\.[0-9]+)?)\b", asset_type.upper())
        if not match:
            return None
        return match.group(1).lower(), match.group(2)

    @staticmethod
    def _collector_number(text: str) -> Optional[str]:
        # Riftbound forms like OGN-304/298 or VEN-190/166.
        match = re.search(r"\b[A-Z]{2,5}-?\d+[A-Za-z]?/\d+\b", text, re.I)
        if match:
            return match.group(0)
        return None

    @staticmethod
    def _normalize_name(text: str) -> str:
        text = text.lower().replace("'", "")
        text = re.sub(r"\bon\b", " ", text)  # shorthand for Overnumbered
        text = re.sub(r"\bpsa\s*\d+(?:\.\d+)?\b", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            for key in ("value", "amount", "price"):
                if key in value:
                    return TcggoPricingProvider._num(value[key])
            return None
        try:
            return float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
