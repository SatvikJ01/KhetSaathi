"""Hit every live tool once for a real location and print a compact summary.

Usage (from backend/):  .venv/bin/python -m scripts.smoke_tools [lat lon crop]
"""
import asyncio
import json
import sys
import time

from app.tools import (
    forecast,
    mandi_prices,
    ndvi_timeseries,
    recent_anomaly,
    reverse_geocode,
    soil_profile,
)


async def main(lat: float, lon: float, crop: str) -> None:
    async def timed(name, coro):
        t = time.perf_counter()
        res = await coro
        print(f"\n=== {name}: ok={res.ok} stale={res.stale} ({time.perf_counter() - t:.1f}s)")
        if res.error:
            print("error:", res.error)
        if res.ok:
            text = json.dumps(res.data.get("summary") or res.data, ensure_ascii=False)
            print(text[:700])
        return res

    loc = await timed("location", reverse_geocode(lat, lon))
    await asyncio.gather(
        timed("weather_forecast", forecast(lat, lon)),
        timed("climate_anomaly", recent_anomaly(lat, lon)),
        timed("soil_profile", soil_profile(lat, lon)),
        timed("crop_ndvi", ndvi_timeseries(lat, lon)),
    )
    if loc.ok:
        await timed("mandi_prices", mandi_prices(crop, loc.data["state"], loc.data["district"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    lat, lon, crop = (float(args[0]), float(args[1]), args[2]) if len(args) == 3 else (20.0857, 74.0093, "onion")
    asyncio.run(main(lat, lon, crop))
