"""Field-level crop vigour (NDVI) time series from Sentinel-2 L2A via Microsoft Planetary Computer.

Only the field's bounding window is read from each cloud-optimised GeoTIFF, so a
90-day series for one farm takes seconds, not a full-tile download. Clouds and
shadows are masked with the Sentinel-2 Scene Classification Layer (SCL).
"""
from __future__ import annotations

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import numpy as np
import planetary_computer
import rasterio
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from app.tools.base import ToolResult, cached_call, round_floats

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
DOC_URL = "https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a"
SCL_CLEAR = {4, 5}  # vegetation, bare soil (excludes cloud, shadow, water, snow)
MIN_VALID_FRACTION = 0.5
MAX_SCENES = 10

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_INGESTED_BYTES_AT_OPEN": "65536",  # grab COG header in one request
    "GDAL_HTTP_VERSION": "2",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "VSI_CACHE": "TRUE",
}
# Each scene = 3 remote band reads; reads are latency-bound, so use many threads.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="s2read")
_SCENE_POOL = ThreadPoolExecutor(max_workers=MAX_SCENES, thread_name_prefix="s2scene")


def field_bbox(lat: float, lon: float, polygon: list[list[float]] | None, buffer_m: float = 60) -> tuple:
    """(minlon, minlat, maxlon, maxlat) of the drawn polygon, or a small square around the pin."""
    if polygon and len(polygon) >= 3:
        lons = [p[0] for p in polygon]
        lats = [p[1] for p in polygon]
        return (min(lons), min(lats), max(lons), max(lats))
    dlat = buffer_m / 111_320
    dlon = buffer_m / (111_320 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _read_band(href: str, bbox: tuple, out_shape: tuple[int, int] | None = None) -> np.ndarray:
    """Read only the field's window of one band. 10 m bands set the grid; SCL (20 m) is resampled onto it."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
        win = from_bounds(*bounds, transform=src.transform)
        if out_shape is None:
            win = win.round_offsets().round_lengths()
            out_shape = (max(1, int(win.height)), max(1, int(win.width)))
        return src.read(1, window=win, out_shape=out_shape, boundless=True, fill_value=0,
                        resampling=Resampling.nearest)


def _scene_ndvi(item: Any, bbox: tuple) -> dict[str, Any] | None:
    props = item.properties
    # Processing baseline >= 04.00 adds a +1000 DN offset to L2A reflectances.
    offset = 1000 if str(props.get("s2:processing_baseline", "00.00")) >= "04.00" else 0
    red_f = _POOL.submit(_read_band, item.assets["B04"].href, bbox)
    nir_f = _POOL.submit(_read_band, item.assets["B08"].href, bbox)
    red = red_f.result().astype("float32")
    scl_f = _POOL.submit(_read_band, item.assets["SCL"].href, bbox, red.shape)
    nir = nir_f.result().astype("float32")
    scl = scl_f.result()
    if nir.shape != red.shape:
        return None

    red = np.clip(red - offset, 0, None)
    nir = np.clip(nir - offset, 0, None)
    valid = np.isin(scl, list(SCL_CLEAR)) & ((red + nir) > 0)
    frac = float(valid.mean()) if valid.size else 0.0
    if frac < MIN_VALID_FRACTION:
        return {"date": props["datetime"][:10], "usable": False, "valid_fraction": frac,
                "scene_cloud_pct": props.get("eo:cloud_cover")}
    ndvi = (nir[valid] - red[valid]) / (nir[valid] + red[valid])
    return {
        "date": props["datetime"][:10],
        "usable": True,
        "ndvi_mean": float(ndvi.mean()),
        "ndvi_p10": float(np.percentile(ndvi, 10)),
        "ndvi_p90": float(np.percentile(ndvi, 90)),
        "valid_fraction": frac,
        "pixels": int(valid.sum()),
        "scene_cloud_pct": props.get("eo:cloud_cover"),
    }


def _search(bbox: tuple, days: int) -> list[Any]:
    catalog = Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    end = date.today()
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{end - timedelta(days=days)}/{end}",
        query={"eo:cloud_cover": {"lt": 70}},
        sortby=[{"field": "properties.datetime", "direction": "desc"}],
        max_items=MAX_SCENES * 2,
    )
    items = list(search.items())
    # One scene per date (adjacent tiles overlap); keep the least cloudy.
    by_date: dict[str, Any] = {}
    for it in items:
        d = it.properties["datetime"][:10]
        if d not in by_date or it.properties["eo:cloud_cover"] < by_date[d].properties["eo:cloud_cover"]:
            by_date[d] = it
    return [by_date[d] for d in sorted(by_date, reverse=True)][:MAX_SCENES]


def _interpret(series: list[dict[str, Any]]) -> dict[str, Any]:
    if not series:
        return {"stage_hint": "no clear scene in window"}
    latest = series[-1]["ndvi_mean"]
    out: dict[str, Any] = {"latest_ndvi": latest, "latest_date": series[-1]["date"]}
    if len(series) >= 3:
        t0 = date.fromisoformat(series[0]["date"])
        x = np.array([(date.fromisoformat(s["date"]) - t0).days for s in series], dtype=float)
        y = np.array([s["ndvi_mean"] for s in series])
        slope = float(np.polyfit(x, y, 1)[0]) * 10 if np.ptp(x) > 0 else 0.0
        out["trend_per_10_days"] = slope
        out["peak_ndvi"] = float(y.max())
        out["peak_date"] = series[int(y.argmax())]["date"]
        out["trend"] = "rising" if slope > 0.02 else "falling" if slope < -0.02 else "stable"
    if latest < 0.2:
        out["stage_hint"] = "bare soil / just sown / harvested"
    elif latest < 0.4:
        out["stage_hint"] = "early growth or sparse / stressed canopy"
    elif latest < 0.6:
        out["stage_hint"] = "moderate canopy"
    else:
        out["stage_hint"] = "dense healthy canopy"
    return out


async def ndvi_timeseries(
    lat: float, lon: float, polygon: list[list[float]] | None = None, days: int = 90
) -> ToolResult:
    bbox = field_bbox(lat, lon, polygon)

    async def fetch() -> ToolResult:
        try:
            items = await asyncio.to_thread(_search, bbox, days)
            if not items:
                return ToolResult("crop_ndvi", "Sentinel-2 L2A", DOC_URL, ok=False,
                                  error=f"no Sentinel-2 scenes under 70% cloud in last {days} days")
            loop = asyncio.get_running_loop()
            scenes = await asyncio.gather(
                *(loop.run_in_executor(_SCENE_POOL, _scene_ndvi, it, bbox) for it in items),
                return_exceptions=True,
            )
        except Exception as exc:  # network / STAC / GDAL errors
            return ToolResult("crop_ndvi", "Sentinel-2 L2A", DOC_URL, ok=False,
                              error=f"{type(exc).__name__}: {exc}")

        good = [s for s in scenes if isinstance(s, dict) and s.get("usable")]
        skipped = [s for s in scenes if isinstance(s, dict) and not s.get("usable")]
        good.sort(key=lambda s: s["date"])
        if not good:
            return ToolResult("crop_ndvi", "Sentinel-2 L2A", DOC_URL, ok=False,
                              error=f"all {len(items)} recent scenes cloudy over this field")
        data = {
            "bbox": bbox,
            "series": good,
            "cloudy_scenes_skipped": [s["date"] for s in skipped],
            "summary": _interpret(good),
        }
        return ToolResult("crop_ndvi", "Sentinel-2 L2A (ESA Copernicus) via Planetary Computer",
                          DOC_URL, ok=True, data=round_floats(data, 3))

    key = f"ndvi:{','.join(f'{v:.5f}' for v in bbox)}:{days}:{date.today()}"
    return await cached_call(key, ttl_s=12 * 3600, fetch=fetch)
