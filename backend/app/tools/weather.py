"""16-day weather forecast from Open-Meteo (keyless, blends ECMWF/GFS/ICON)."""
from __future__ import annotations

from app.tools.base import ToolError, ToolResult, cached_call, get_json, round_floats

URL = "https://api.open-meteo.com/v1/forecast"
DOC_URL = "https://open-meteo.com/en/docs"

# IMD rainfall intensity categories (mm/day): moderate 15.6-64.4, heavy 64.5-115.5
MODERATE_RAIN_MM = 15.6
HEAVY_RAIN_MM = 64.5
HEAT_TMAX_C = 40.0  # IMD heatwave threshold for plains
STRONG_WIND_KMH = 40.0


async def forecast(lat: float, lon: float, days: int = 16) -> ToolResult:
    days = max(1, min(days, 16))

    async def fetch() -> ToolResult:
        params = {
            "latitude": lat,
            "longitude": lon,
            "timezone": "Asia/Kolkata",
            "forecast_days": days,
            "daily": ",".join(
                [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                    "et0_fao_evapotranspiration",
                    "relative_humidity_2m_mean",
                ]
            ),
        }
        try:
            raw = await get_json(URL, params=params)
        except ToolError as exc:
            return ToolResult("weather_forecast", "Open-Meteo", DOC_URL, ok=False, error=str(exc))

        d = raw["daily"]
        rows = [
            {
                "date": d["time"][i],
                "tmax_c": d["temperature_2m_max"][i],
                "tmin_c": d["temperature_2m_min"][i],
                "rain_mm": d["precipitation_sum"][i],
                "rain_prob_pct": d["precipitation_probability_max"][i],
                "wind_max_kmh": d["wind_speed_10m_max"][i],
                "et0_mm": d["et0_fao_evapotranspiration"][i],
                "rh_mean_pct": d["relative_humidity_2m_mean"][i],
            }
            for i in range(len(d["time"]))
        ]
        alerts = []
        for r in rows:
            rain = r["rain_mm"] or 0
            if rain >= HEAVY_RAIN_MM:
                alerts.append({"date": r["date"], "type": "heavy_rain", "value_mm": rain})
            elif rain >= MODERATE_RAIN_MM:
                alerts.append({"date": r["date"], "type": "moderate_rain", "value_mm": rain})
            if (r["tmax_c"] or 0) >= HEAT_TMAX_C:
                alerts.append({"date": r["date"], "type": "heat", "value_c": r["tmax_c"]})
            if (r["wind_max_kmh"] or 0) >= STRONG_WIND_KMH:
                alerts.append({"date": r["date"], "type": "strong_wind", "value_kmh": r["wind_max_kmh"]})

        week = rows[:7]
        summary = {
            "next7_rain_total_mm": sum(r["rain_mm"] or 0 for r in week),
            "next7_max_tmax_c": max(r["tmax_c"] for r in week),
            "next7_min_tmin_c": min(r["tmin_c"] for r in week),
            "next7_et0_total_mm": sum(r["et0_mm"] or 0 for r in week),
            "dry_days_next7": sum(1 for r in week if (r["rain_mm"] or 0) < 1),
        }
        return ToolResult(
            "weather_forecast",
            "Open-Meteo forecast",
            DOC_URL,
            ok=True,
            data=round_floats({"daily": rows, "summary": summary, "alerts": alerts}, 1),
        )

    return await cached_call(f"forecast:{lat:.3f}:{lon:.3f}:{days}", ttl_s=1800, fetch=fetch)
