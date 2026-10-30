"""FastAPI entrypoint: agent Q&A (streamed trace) and field dashboard data."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from app.agent.graph import GRAPH
from app.alerts import scan_field
from app.config import get_settings
from app.tools import (
    agronomy_docs,
    forecast,
    mandi_prices,
    ndvi_timeseries,
    recent_anomaly,
    reverse_geocode,
    soil_profile,
)
from app.voice import VoiceError, synthesize, transcribe

MAX_AUDIO_BYTES = 5 * 1024 * 1024
log = logging.getLogger("khetsaathi")


async def prewarm() -> None:
    """Fill caches for the demo field and load the RAG index in the background."""
    s = get_settings()
    lat, lon = s.demo_lat, s.demo_lon
    t0 = time.perf_counter()
    loc = await reverse_geocode(lat, lon)
    jobs = [forecast(lat, lon), recent_anomaly(lat, lon), soil_profile(lat, lon), ndvi_timeseries(lat, lon),
            agronomy_docs("onion harvest curing storage")]
    if loc.ok and s.data_gov_api_key:
        jobs.append(mandi_prices(s.demo_crop, loc.data["state"], loc.data["district"]))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    ok = sum(1 for r in results if not isinstance(r, BaseException) and r.ok)
    log.warning("prewarm: %d/%d demo-field tools cached in %.0fs", ok, len(results), time.perf_counter() - t0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(prewarm()) if get_settings().prewarm else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="KhetSaathi API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


class FieldIn(BaseModel):
    lat: float = Field(ge=6.0, le=37.5)  # India bounding box
    lon: float = Field(ge=68.0, le=97.5)
    polygon: list[list[float]] | None = None

    @field_validator("polygon")
    @classmethod
    def _small_field(cls, poly: list[list[float]] | None) -> list[list[float]] | None:
        if poly is None:
            return None
        if len(poly) < 3:
            raise ValueError("polygon needs at least 3 points")
        lons = [p[0] for p in poly]
        lats = [p[1] for p in poly]
        if max(lons) - min(lons) > 0.05 or max(lats) - min(lats) > 0.05:
            raise ValueError("field polygon too large (max ~5 km across)")
        return poly


class AskIn(FieldIn):
    question: str = Field(min_length=2, max_length=2000)
    crop: str | None = Field(default=None, max_length=60)


def _llm_configured() -> bool:
    s = get_settings()
    return bool({"gemini": s.google_api_key, "openai": s.openai_api_key,
                 "anthropic": s.anthropic_api_key}.get(s.llm_provider.lower()))


@app.get("/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {"ok": True, "llm_provider": s.llm_provider, "llm_configured": _llm_configured(),
            "mandi_configured": bool(s.data_gov_api_key), "voice_configured": bool(s.sarvam_api_key)}


FIELD_TOOLS = {
    "location": lambda b: reverse_geocode(b.lat, b.lon),
    "weather_forecast": lambda b: forecast(b.lat, b.lon),
    "climate_anomaly": lambda b: recent_anomaly(b.lat, b.lon),
    "soil_profile": lambda b: soil_profile(b.lat, b.lon),
    "crop_ndvi": lambda b: ndvi_timeseries(b.lat, b.lon, b.polygon),
}


@app.post("/api/field/{tool}")
async def field_tool(tool: str, body: FieldIn) -> dict[str, Any]:
    """One dashboard card's live data, so the UI can render each card as soon as it arrives."""
    if tool not in FIELD_TOOLS:
        raise HTTPException(404, f"unknown tool {tool!r}; choose from {sorted(FIELD_TOOLS)}")
    try:
        return (await FIELD_TOOLS[tool](body)).to_dict()
    except Exception as exc:
        return {"tool": tool, "ok": False, "error": f"{type(exc).__name__}: {exc}", "data": {}}


@app.post("/api/alerts/scan")
async def alerts_scan(body: FieldIn) -> dict[str, Any]:
    """Rule-based risk scan for one field over the next few days (no LLM)."""
    return await scan_field(body.lat, body.lon, body.polygon)


@app.post("/api/ask")
async def ask(body: AskIn) -> EventSourceResponse:
    """Run the agent; stream trace events, then the final answer, as Server-Sent Events."""
    if not _llm_configured():
        raise HTTPException(503, f"LLM not configured: set the API key for LLM_PROVIDER={get_settings().llm_provider}")
    state = {"question": body.question.strip(), "lat": body.lat, "lon": body.lon,
             "polygon": body.polygon, "crop_hint": body.crop}

    async def events():
        try:
            async for event in GRAPH.astream(state, stream_mode="custom"):
                yield {"event": event["type"], "data": json.dumps(event, ensure_ascii=False, default=str)}
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"type": "error", "msg": f"{type(exc).__name__}: {exc}"})}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="hi", max_length=5)


@app.post("/api/voice/stt")
async def voice_stt(audio: UploadFile = File(...)) -> dict[str, Any]:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio too long; keep voice notes under ~30 seconds")
    try:
        return await transcribe(data, audio.filename or "voice.webm", audio.content_type or "audio/webm")
    except VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/voice/tts")
async def voice_tts(body: TtsIn) -> dict[str, str]:
    spoken = re.sub(r"\[T\d+\]", "", body.text)  # citation tags are for the screen, not the ear
    spoken = re.sub(r"[*#_`]", "", spoken)
    try:
        return {"audio_base64": await synthesize(spoken, body.language), "format": "wav"}
    except VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc


# In production the built React app is served from the same origin (one URL, no CORS).
# Mounted last so it never shadows the API routes above.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
