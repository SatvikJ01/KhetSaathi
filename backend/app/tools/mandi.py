"""Live wholesale mandi prices from AGMARKNET via the data.gov.in OGD API.

Needs a free personal key (DATA_GOV_API_KEY). Without one the tool fails loudly;
it never substitutes made-up prices.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.config import get_settings
from app.tools.base import ToolError, ToolResult, cached_call, get_json

RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
DOC_URL = "https://data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi"

# Common names -> AGMARKNET commodity names
COMMODITY_ALIASES = {
    "paddy": "Paddy(Dhan)(Common)",
    "rice": "Paddy(Dhan)(Common)",
    "soybean": "Soyabean",
    "soya": "Soyabean",
    "chilli": "Green Chilli",
    "groundnut": "Groundnut",
    "cotton": "Cotton",
    "maize": "Maize",
    "corn": "Maize",
    "gram": "Bengal Gram(Gram)(Whole)",
    "chana": "Bengal Gram(Gram)(Whole)",
    "tur": "Arhar (Tur/Red Gram)(Whole)",
    "arhar": "Arhar (Tur/Red Gram)(Whole)",
    "mustard": "Mustard",
    "bajra": "Bajra(Pearl Millet/Cumbu)",
    "jowar": "Jowar(Sorghum)",
}


def _commodity_name(crop: str) -> str:
    key = crop.strip().lower()
    return COMMODITY_ALIASES.get(key, crop.strip().title())


async def _query(params: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    base = {"api-key": settings.data_gov_api_key, "format": "json", "limit": 1000}
    raw = await get_json(URL, params={**base, **params})
    return raw.get("records", [])


async def mandi_prices(crop: str, state: str, district: str | None = None) -> ToolResult:
    if not get_settings().data_gov_api_key:
        return ToolResult("mandi_prices", "AGMARKNET / data.gov.in", DOC_URL, ok=False,
                          error="DATA_GOV_API_KEY not configured")
    commodity = _commodity_name(crop)

    async def fetch() -> ToolResult:
        try:
            records = await _query({"filters[state.keyword]": state, "filters[commodity]": commodity})
            if not records:  # fall back to fuzzy commodity match within the state
                all_rows = await _query({"filters[state.keyword]": state})
                needle = crop.strip().lower()
                records = [r for r in all_rows if needle in r.get("commodity", "").lower()]
        except ToolError as exc:
            return ToolResult("mandi_prices", "AGMARKNET / data.gov.in", DOC_URL, ok=False, error=str(exc))

        if not records:
            return ToolResult("mandi_prices", "AGMARKNET / data.gov.in", DOC_URL, ok=False,
                              error=f"no {commodity} arrivals reported in {state} today")

        markets: dict[str, dict[str, Any]] = {}
        for r in records:
            name = f"{r['market']} ({r['district']})"
            modal = float(r["modal_price"])
            # keep the highest-priced variety per market
            if name not in markets or modal > markets[name]["modal_price_rs_qtl"]:
                markets[name] = {
                    "market": r["market"],
                    "district": r["district"],
                    "variety": r.get("variety"),
                    "arrival_date": r.get("arrival_date"),
                    "min_price_rs_qtl": float(r["min_price"]),
                    "max_price_rs_qtl": float(r["max_price"]),
                    "modal_price_rs_qtl": modal,
                    "in_user_district": bool(district) and district.lower() in r["district"].lower(),
                }
        ranked = sorted(markets.values(), key=lambda m: m["modal_price_rs_qtl"], reverse=True)
        by_district: dict[str, list[float]] = defaultdict(list)
        for m in ranked:
            by_district[m["district"]].append(m["modal_price_rs_qtl"])
        local = [m for m in ranked if m["in_user_district"]]
        data = {
            "commodity": commodity,
            "state": state,
            "unit": "Rs per quintal",
            "markets_reporting": len(ranked),
            "top_markets": ranked[:8],
            "local_markets": local[:5],
            "state_modal_median_rs_qtl": sorted(m["modal_price_rs_qtl"] for m in ranked)[len(ranked) // 2],
            "best_vs_local_gap_rs_qtl": (ranked[0]["modal_price_rs_qtl"] - local[0]["modal_price_rs_qtl"])
            if local else None,
        }
        return ToolResult("mandi_prices", "AGMARKNET daily prices (data.gov.in OGD)", DOC_URL,
                          ok=True, data=data)

    return await cached_call(f"mandi:{commodity}:{state}:{district}", ttl_s=3 * 3600, fetch=fetch)
