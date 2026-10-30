"""Topsoil properties at the exact field location from ISRIC SoilGrids 2.0 (keyless, 250 m)."""
from __future__ import annotations

from app.tools.base import ToolError, ToolResult, cached_call, get_json, round_floats

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DOC_URL = "https://www.isric.org/explore/soilgrids"

# SoilGrids property -> (output name, unit after dividing by d_factor)
PROPS = {
    "phh2o": ("ph", "pH"),
    "soc": ("organic_carbon", "g/kg"),
    "nitrogen": ("nitrogen", "g/kg"),
    "clay": ("clay", "%"),  # g/kg / 10
    "sand": ("sand", "%"),
    "silt": ("silt", "%"),
    "cec": ("cec", "cmol(c)/kg"),
}
DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]


def _texture(clay: float, sand: float) -> str:
    """Coarse USDA texture class from clay/sand %."""
    if clay >= 40:
        return "clay"
    if clay >= 27:
        return "clay loam" if sand < 45 else "sandy clay loam"
    if sand >= 70:
        return "sandy / loamy sand"
    if sand >= 45:
        return "sandy loam"
    return "loam / silt loam"


async def soil_profile(lat: float, lon: float) -> ToolResult:
    async def fetch() -> ToolResult:
        params = [("lon", lon), ("lat", lat), ("value", "mean")]
        params += [("property", p) for p in PROPS] + [("depth", d) for d in DEPTHS]
        try:
            raw = await get_json(URL, params=params, retries=3)
        except ToolError as exc:
            return ToolResult("soil_profile", "ISRIC SoilGrids", DOC_URL, ok=False, error=str(exc))

        out: dict[str, dict[str, float | None]] = {}
        units: dict[str, str] = {}
        for layer in raw["properties"]["layers"]:
            name, unit = PROPS[layer["name"]]
            factor = layer["unit_measure"]["d_factor"]
            units[name] = unit
            for depth in layer["depths"]:
                val = depth["values"]["mean"]
                out.setdefault(depth["label"], {})[name] = None if val is None else val / factor

        top = out.get("0-5cm", {})
        if top.get("ph") is None:
            return ToolResult("soil_profile", "ISRIC SoilGrids", DOC_URL, ok=False,
                              error="no soil data at this point (water body / urban / outside coverage)")

        notes = []
        ph = top["ph"]
        if ph < 5.5:
            notes.append("strongly acidic topsoil")
        elif ph > 8.0:
            notes.append("alkaline topsoil")
        if (top.get("organic_carbon") or 0) < 5:
            notes.append("low organic carbon")
        data = {
            "by_depth": out,
            "units": units,
            "texture_0_5cm": _texture(top.get("clay") or 0, top.get("sand") or 0),
            "notes": notes,
            "resolution": "250 m modelled estimate, not a lab soil test",
        }
        return ToolResult("soil_profile", "ISRIC SoilGrids 2.0", DOC_URL, ok=True,
                          data=round_floats(data, 2))

    return await cached_call(f"soil:{lat:.3f}:{lon:.3f}", ttl_s=30 * 86400, fetch=fetch)
