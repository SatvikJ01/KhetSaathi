"""Recent observed weather vs. long-term normal, from NASA POWER (keyless).

Answers "has it been unusually dry/wet/hot here lately?" at the farm's location,
which a district-level advisory cannot.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import date, timedelta

from app.tools.base import ToolError, ToolResult, cached_call, get_json, round_floats

DAILY_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
CLIM_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
DOC_URL = "https://power.larc.nasa.gov/docs/services/api/"
MISSING = -999.0
MONTHS = [m.upper() for m in calendar.month_abbr[1:]]  # JAN..DEC


async def recent_anomaly(lat: float, lon: float, window_days: int = 30) -> ToolResult:
    async def fetch() -> ToolResult:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=window_days + 10)  # POWER lags a few days
        common = {"community": "AG", "latitude": lat, "longitude": lon, "format": "JSON"}
        params = "PRECTOTCORR,T2M_MAX,T2M_MIN"
        try:
            daily, clim = await asyncio.gather(
                get_json(
                    DAILY_URL,
                    params={**common, "parameters": params,
                            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")},
                ),
                get_json(CLIM_URL, params={**common, "parameters": "PRECTOTCORR,T2M_MAX"}),
            )
        except ToolError as exc:
            return ToolResult("climate_anomaly", "NASA POWER", DOC_URL, ok=False, error=str(exc))

        p = daily["properties"]["parameter"]
        days = sorted(
            k for k, v in p["PRECTOTCORR"].items()
            if v != MISSING and p["T2M_MAX"].get(k, MISSING) != MISSING
        )[-window_days:]
        if len(days) < window_days // 2:
            return ToolResult("climate_anomaly", "NASA POWER", DOC_URL, ok=False,
                              error="too few observed days available yet")

        cp = clim["properties"]["parameter"]
        rain_obs = sum(p["PRECTOTCORR"][d] for d in days)
        rain_norm = sum(cp["PRECTOTCORR"][MONTHS[int(d[4:6]) - 1]] for d in days)
        tmax_obs = sum(p["T2M_MAX"][d] for d in days) / len(days)
        tmax_norm = sum(cp["T2M_MAX"][MONTHS[int(d[4:6]) - 1]] for d in days) / len(days)
        rain_pct = 100 * rain_obs / rain_norm if rain_norm > 0.5 else None

        # IMD-style rainfall departure categories
        if rain_pct is None:
            category = "normal is ~0 mm for this period"
        elif rain_pct < 40:
            category = "large deficient"
        elif rain_pct < 80:
            category = "deficient"
        elif rain_pct <= 120:
            category = "normal"
        else:
            category = "excess"

        data = {
            "period_start": f"{days[0][:4]}-{days[0][4:6]}-{days[0][6:]}",
            "period_end": f"{days[-1][:4]}-{days[-1][4:6]}-{days[-1][6:]}",
            "observed_days": len(days),
            "rain_observed_mm": rain_obs,
            "rain_normal_mm": rain_norm,
            "rain_pct_of_normal": rain_pct,
            "rain_category": category,
            "tmax_mean_observed_c": tmax_obs,
            "tmax_mean_normal_c": tmax_norm,
            "tmax_departure_c": tmax_obs - tmax_norm,
            "last7_rain_mm": sum(p["PRECTOTCORR"][d] for d in days[-7:]),
        }
        return ToolResult("climate_anomaly", "NASA POWER (observed vs 30-yr normal)", DOC_URL,
                          ok=True, data=round_floats(data, 1))

    return await cached_call(f"anomaly:{lat:.2f}:{lon:.2f}:{window_days}:{date.today()}",
                             ttl_s=6 * 3600, fetch=fetch)
