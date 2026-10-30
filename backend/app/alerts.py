"""Proactive field risk scan: deterministic rules over live tool data.

Runs without the LLM so it can be scheduled cheaply for many fields; the agent is
only invoked to explain an alert to the farmer in their language.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.tools import forecast, ndvi_timeseries, recent_anomaly

HORIZON_DAYS = 5  # alerts only for the actionable window


async def scan_field(lat: float, lon: float, polygon: list[list[float]] | None = None) -> dict[str, Any]:
    fc, anom, ndvi = await asyncio.gather(
        forecast(lat, lon), recent_anomaly(lat, lon), ndvi_timeseries(lat, lon, polygon)
    )
    alerts: list[dict[str, Any]] = []

    if fc.ok:
        window = {r["date"] for r in fc.data["daily"][:HORIZON_DAYS]}
        for a in fc.data["alerts"]:
            if a["date"] not in window:
                continue
            severity = "high" if a["type"] in ("heavy_rain", "heat") else "medium"
            alerts.append({"type": a["type"], "date": a["date"], "severity": severity,
                           "evidence": a, "source": fc.source})
        s = fc.data["summary"]
        if anom.ok and anom.data["rain_category"] in ("deficient", "large deficient") and s["dry_days_next7"] >= 6:
            alerts.append({"type": "moisture_stress", "severity": "medium",
                           "evidence": {"rain_pct_of_normal": anom.data["rain_pct_of_normal"],
                                        "dry_days_next7": s["dry_days_next7"],
                                        "next7_et0_total_mm": s["next7_et0_total_mm"]},
                           "source": f"{anom.source} + {fc.source}"})

    if ndvi.ok:
        summ = ndvi.data["summary"]
        if summ.get("trend") == "falling" and summ.get("peak_ndvi", 0) - summ["latest_ndvi"] > 0.1:
            alerts.append({"type": "vigour_drop", "severity": "medium",
                           "evidence": {k: summ.get(k) for k in ("peak_ndvi", "peak_date", "latest_ndvi",
                                                                  "latest_date", "trend_per_10_days")},
                           "source": ndvi.source})

    failed = [r.tool for r in (fc, anom, ndvi) if not r.ok]
    return {"lat": lat, "lon": lon, "alerts": alerts, "unavailable": failed}
