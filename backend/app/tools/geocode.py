"""Reverse geocoding (lat/lon -> village, district, state) via OpenStreetMap Nominatim."""
from __future__ import annotations

from app.tools.base import ToolError, ToolResult, cached_call, get_json

URL = "https://nominatim.openstreetmap.org/reverse"
DOC_URL = "https://nominatim.org/release-docs/latest/api/Reverse/"


def _clean_district(name: str | None) -> str | None:
    if not name:
        return None
    for suffix in (" District", " district", " Tahsil", " Taluka"):
        name = name.removesuffix(suffix)
    return name.strip()


async def reverse_geocode(lat: float, lon: float) -> ToolResult:
    async def fetch() -> ToolResult:
        try:
            raw = await get_json(
                URL,
                params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14,
                        "accept-language": "en"},
            )
        except ToolError as exc:
            return ToolResult("location", "OpenStreetMap Nominatim", DOC_URL, ok=False, error=str(exc))
        addr = raw.get("address", {})
        if addr.get("country_code") != "in":
            return ToolResult("location", "OpenStreetMap Nominatim", DOC_URL, ok=False,
                              error="location is outside India")
        data = {
            "village": addr.get("village") or addr.get("town") or addr.get("city") or addr.get("hamlet"),
            "subdistrict": addr.get("county") or addr.get("subdistrict"),
            "district": _clean_district(addr.get("state_district") or addr.get("county")),
            "state": addr.get("state"),
            "display_name": raw.get("display_name"),
        }
        return ToolResult("location", "OpenStreetMap Nominatim", DOC_URL, ok=True, data=data)

    return await cached_call(f"geo:{lat:.4f}:{lon:.4f}", ttl_s=90 * 86400, fetch=fetch)
