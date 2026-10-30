"""Search official agronomy guides (ICAR-DOGR, MPKV Rahuri) for practices, varieties, doses."""
from __future__ import annotations

import asyncio

from app.rag import get_index
from app.tools.base import ToolResult

SOURCE = "Official agronomy guides (ICAR-DOGR, MPKV Rahuri)"
DOC_URL = "https://epubs.icar.org.in/index.php/IndHort"


async def agronomy_docs(query: str, k: int = 4) -> ToolResult:
    try:
        hits, mode = await asyncio.to_thread(get_index().search, query, k)
    except Exception as exc:
        return ToolResult("agronomy_docs", SOURCE, DOC_URL, ok=False, error=f"{type(exc).__name__}: {exc}")
    if not hits:
        return ToolResult("agronomy_docs", SOURCE, DOC_URL, ok=False,
                          error=f"no passage in the official guides matches {query!r}")
    return ToolResult("agronomy_docs", SOURCE, DOC_URL, ok=True,
                      data={"query": query, "retrieval": mode, "passages": hits})
