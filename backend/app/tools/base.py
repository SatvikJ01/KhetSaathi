"""Shared plumbing for live-data tools: result envelope, HTTP with retries, cache.

Every tool returns a ToolResult. The agent only ever reasons over ToolResult.data,
and cites ToolResult.id, so every number shown to the farmer is traceable to a
live API call (or an explicitly labelled stale cached copy of one).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.config import get_settings

USER_AGENT = "KhetSaathi/0.1 (Forge26 buildathon; contact via GitHub repo)"


@dataclass
class ToolResult:
    tool: str
    source: str  # human-readable data source name
    url: str  # canonical source/documentation URL for citation
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stale: bool = False  # True when served from cache after a live failure
    id: str = ""  # citation id assigned by the agent, e.g. "T1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolError(Exception):
    pass


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    json_body: Any = None,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> Any:
    """HTTP request with timeout and exponential-backoff retries on 5xx/network errors."""
    settings = get_settings()
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.http_timeout_s, headers=hdrs) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.request(method, url, params=params, json=json_body)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise ToolError(f"HTTP {resp.status_code} from {url}")
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, ToolError) as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.8 * 2**attempt)
            except (httpx.HTTPStatusError, ValueError) as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    raise ToolError(str(last_exc))


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    path = Path(get_settings().cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{digest}.json"


async def cached_call(
    key: str,
    ttl_s: int,
    fetch: Callable[[], Awaitable[ToolResult]],
) -> ToolResult:
    """Serve a fresh cached result if within TTL, else fetch live.

    If the live fetch fails and an older cached copy exists, return it marked
    stale (with the original fetch time) instead of failing the whole answer.
    """
    path = _cache_path(key)
    cached: dict[str, Any] | None = None
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = None
    if cached and time.time() - cached["_saved_at"] < ttl_s:
        return ToolResult(**cached["result"])

    result = await fetch()
    if result.ok:
        path.write_text(json.dumps({"_saved_at": time.time(), "result": result.to_dict()}))
        return result
    if cached:
        stale = ToolResult(**cached["result"])
        stale.stale = True
        stale.error = f"live call failed ({result.error}); showing data fetched at {stale.fetched_at}"
        return stale
    return result


def round_floats(obj: Any, ndigits: int = 2) -> Any:
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(v, ndigits) for v in obj]
    return obj
