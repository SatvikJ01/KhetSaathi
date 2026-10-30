"""Tool registry: the agent's planner chooses from these by name."""
from app.tools.base import ToolResult
from app.tools.climate import recent_anomaly
from app.tools.geocode import reverse_geocode
from app.tools.mandi import mandi_prices
from app.tools.ndvi import ndvi_timeseries
from app.tools.soil import soil_profile
from app.tools.weather import forecast

TOOL_DESCRIPTIONS = {
    "weather_forecast": "16-day daily forecast at the field: rain, temperature, wind, ET0, rain alerts.",
    "climate_anomaly": "Last 30 days observed rain/temperature at the field vs 30-year normal.",
    "soil_profile": "Topsoil pH, organic carbon, nitrogen, texture at the field (SoilGrids).",
    "crop_ndvi": "Sentinel-2 satellite crop vigour (NDVI) series for the field, last 90 days, cloud-masked.",
    "mandi_prices": "Today's wholesale prices for the crop across mandis in the farmer's state.",
}

__all__ = [
    "TOOL_DESCRIPTIONS",
    "ToolResult",
    "forecast",
    "mandi_prices",
    "ndvi_timeseries",
    "recent_anomaly",
    "reverse_geocode",
    "soil_profile",
]
