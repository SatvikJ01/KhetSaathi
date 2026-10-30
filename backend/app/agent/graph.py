"""KhetSaathi agent as a LangGraph state machine.

    understand ──(off_topic)──────────────────────────────► finalize
        │
        ▼
    run_tools ──► draft ──► verify ──(ok or out of retries)──► finalize
                    ▲          │
                    └─(issues)─┘   (max MAX_DRAFTS attempts)

Every node emits trace events through the LangGraph stream writer so the UI can
show the agent's reasoning and tool calls live.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent.llm import get_runnable
from app.agent.prompts import DRAFT_SYSTEM, FEEDBACK_BLOCK, OFF_TOPIC_REPLY, UNDERSTAND_SYSTEM
from app.agent.verify import verify_draft
from app.tools import (
    TOOL_DESCRIPTIONS,
    agronomy_docs,
    forecast,
    mandi_prices,
    ndvi_timeseries,
    recent_anomaly,
    reverse_geocode,
    soil_profile,
)

MAX_DRAFTS = 3
ToolName = Literal["weather_forecast", "climate_anomaly", "soil_profile", "crop_ndvi", "mandi_prices",
                   "agronomy_docs"]
TOOL_ORDER: list[str] = list(TOOL_DESCRIPTIONS)
DOC_INTENTS = {"pest_disease", "fertilizer", "sowing", "harvest_timing"}


class Understanding(BaseModel):
    language: str = Field(description="ISO 639-1 code of the farmer's language")
    crop: str | None = Field(default=None, description="crop in English, singular")
    intent: Literal["harvest_timing", "sowing", "irrigation", "pest_disease", "fertilizer",
                    "selling", "weather", "general", "off_topic"]
    tools: list[ToolName]
    doc_query: str | None = Field(
        default=None, description="English search query for the agronomy guides, if agronomy_docs is used")
    plan: str


class AgentState(TypedDict, total=False):
    question: str
    lat: float
    lon: float
    polygon: list[list[float]] | None
    crop_hint: str | None
    understanding: dict[str, Any]
    location: dict[str, Any]
    results: dict[str, dict[str, Any]]
    draft: str
    attempts: int
    verification: dict[str, Any]
    answer: dict[str, Any]


def _emit(event: dict[str, Any]) -> None:
    get_stream_writer()({"ts": time.time(), **event})


# ---------------------------------------------------------------- nodes

async def understand(state: AgentState) -> AgentState:
    _emit({"type": "node", "node": "understand", "msg": "Understanding the question and planning tools"})
    tools_txt = "\n".join(f"  - {k}: {v}" for k, v in TOOL_DESCRIPTIONS.items())
    llm = get_runnable(Understanding)
    try:
        u: Understanding = await llm.ainvoke([
            SystemMessage(UNDERSTAND_SYSTEM.format(tools=tools_txt, crop_hint=state.get("crop_hint") or "")),
            HumanMessage(state["question"]),
        ])
        result = u.model_dump()
    except Exception as exc:  # planner failure must not kill the request
        _emit({"type": "warning", "node": "understand", "msg": f"Planner failed ({exc}); using default plan"})
        result = {"language": "hi", "crop": state.get("crop_hint"), "intent": "general",
                  "tools": ["weather_forecast", "climate_anomaly", "crop_ndvi"],
                  "plan": "Fallback plan: check weather, recent rain and crop health."}

    if not result.get("crop") and state.get("crop_hint"):
        result["crop"] = state["crop_hint"]
    if result["intent"] != "off_topic" and not result["tools"]:
        result["tools"] = ["weather_forecast"]
    if "mandi_prices" in result["tools"] and not result.get("crop"):
        result["tools"].remove("mandi_prices")
    # Treatment / practice questions must be grounded in official guides, never model memory.
    if result["intent"] in DOC_INTENTS and "agronomy_docs" not in result["tools"]:
        result["tools"].append("agronomy_docs")
    if "agronomy_docs" in result["tools"] and not result.get("doc_query"):
        result["doc_query"] = f"{result.get('crop') or ''} {result['intent'].replace('_', ' ')}".strip()
    _emit({"type": "plan", "node": "understand", "understanding": result})
    return {"understanding": result}


async def run_tools(state: AgentState) -> AgentState:
    lat, lon = state["lat"], state["lon"]
    u = state["understanding"]
    planned = [t for t in TOOL_ORDER if t in u["tools"]]
    _emit({"type": "node", "node": "run_tools", "msg": f"Calling {len(planned) + 1} tools in parallel"})

    async def call(name: str, coro) -> tuple[str, dict[str, Any]]:
        _emit({"type": "tool_start", "tool": name})
        t0 = time.perf_counter()
        try:
            res = (await coro).to_dict()
        except Exception as exc:  # a tool bug must degrade, not crash
            res = {"tool": name, "source": name, "url": "", "ok": False, "data": {},
                   "error": f"{type(exc).__name__}: {exc}", "stale": False, "fetched_at": ""}
        res["latency_s"] = round(time.perf_counter() - t0, 2)
        _emit({"type": "tool_end", "tool": name, "ok": res["ok"], "stale": res["stale"],
               "error": res["error"], "latency_s": res["latency_s"]})
        return name, res

    loc_name, location = await call("location", reverse_geocode(lat, lon))

    factories = {
        "weather_forecast": lambda: forecast(lat, lon),
        "climate_anomaly": lambda: recent_anomaly(lat, lon),
        "soil_profile": lambda: soil_profile(lat, lon),
        "crop_ndvi": lambda: ndvi_timeseries(lat, lon, state.get("polygon")),
        "agronomy_docs": lambda: agronomy_docs(u.get("doc_query") or state["question"]),
    }
    if "mandi_prices" in planned:
        if location["ok"]:
            factories["mandi_prices"] = lambda: mandi_prices(
                u["crop"], location["data"]["state"], location["data"]["district"])
        else:
            planned.remove("mandi_prices")
    pairs = await asyncio.gather(*(call(n, factories[n]()) for n in planned))

    results = {loc_name: location, **dict(pairs)}
    for i, name in enumerate(["location", *planned], start=1):
        results[name]["id"] = f"T{i}"
    return {"results": results, "location": location}


def _evidence_block(results: dict[str, dict[str, Any]]) -> str:
    parts = []
    for r in sorted(results.values(), key=lambda r: int(r["id"][1:])):
        head = f"[{r['id']}] {r['tool']} | source: {r['source']} | fetched: {r['fetched_at'][:16]}"
        if r["ok"] and r["tool"] == "agronomy_docs":
            passages = "\n".join(
                f"- ({p['publisher']} {p['year']}, \"{p['title']}\", p.{p['page']}): {p['text']}"
                for p in r["data"]["passages"])
            parts.append(f"{head}\nOFFICIAL GUIDE PASSAGES for query {r['data']['query']!r}:\n{passages}")
        elif r["ok"]:
            stale = " | STALE CACHED COPY" if r["stale"] else ""
            parts.append(f"{head}{stale}\n{json.dumps(r['data'], ensure_ascii=False)}")
        else:
            parts.append(f"{head}\nFAILED: {r['error']}")
    return "\n\n".join(parts)


async def draft(state: AgentState) -> AgentState:
    attempts = state.get("attempts", 0) + 1
    _emit({"type": "node", "node": "draft", "msg": f"Reasoning over evidence (attempt {attempts})"})
    u = state["understanding"]
    loc = state["location"]
    place = loc["data"].get("display_name") if loc["ok"] else f"{state['lat']:.4f}, {state['lon']:.4f}"
    prev = state.get("verification")
    feedback = FEEDBACK_BLOCK.format(issues="\n".join(f"- {i}" for i in prev["issues"])) if prev else ""
    prompt = DRAFT_SYSTEM.format(
        language=u["language"], place=place, crop=u.get("crop") or "not specified",
        question=state["question"], evidence=_evidence_block(state["results"]), feedback=feedback,
    )
    try:
        msg = await get_runnable().ainvoke([SystemMessage(prompt), HumanMessage(state["question"])])
    except Exception as exc:
        _emit({"type": "warning", "node": "draft",
               "msg": f"All language models unavailable ({type(exc).__name__}); showing data-only summary"})
        return {"draft": _data_only_answer(state["results"]), "attempts": MAX_DRAFTS}
    text = msg.content if isinstance(msg.content, str) else "".join(
        p.get("text", "") for p in msg.content if isinstance(p, dict))
    # Models sometimes write "[T2, T4]"; normalise to "[T2][T4]" for the verifier and UI chips.
    text = re.sub(r"\[(T\d+(?:\s*,\s*T\d+)+)\]",
                  lambda m: "".join(f"[{t.strip()}]" for t in m.group(1).split(",")), text)
    return {"draft": text.strip(), "attempts": attempts}


def _data_only_answer(results: dict[str, dict[str, Any]]) -> str:
    """Deterministic fallback when no LLM responds: key facts straight from tool data, cited."""
    lines = ["AI advisor is busy right now. Here is the live data for your field:"]
    fc = results.get("weather_forecast")
    if fc and fc["ok"]:
        s = fc["data"]["summary"]
        lines.append(f"- Next 7 days: {s['next7_rain_total_mm']} mm rain, max {s['next7_max_tmax_c']}°C [{fc['id']}]")
        for a in fc["data"]["alerts"][:3]:
            lines.append(f"- {a['date']}: {a['type'].replace('_', ' ')} [{fc['id']}]")
    an = results.get("climate_anomaly")
    if an and an["ok"]:
        lines.append(f"- Last 30 days rain: {an['data']['rain_observed_mm']} mm, "
                     f"{an['data']['rain_category']} [{an['id']}]")
    nd = results.get("crop_ndvi")
    if nd and nd["ok"]:
        s = nd["data"]["summary"]
        lines.append(f"- Satellite crop vigour (NDVI) {s['latest_ndvi']} on {s['latest_date']}, "
                     f"{s.get('trend', 'n/a')} [{nd['id']}]")
    md = results.get("mandi_prices")
    if md and md["ok"] and md["data"]["top_markets"]:
        m = md["data"]["top_markets"][0]
        lines.append(f"- Best mandi today: {m['market']} at Rs {m['modal_price_rs_qtl']}/qtl [{md['id']}]")
    return "\n".join(lines)


async def verify(state: AgentState) -> AgentState:
    v = verify_draft(state["draft"], state["results"], state["question"])
    _emit({"type": "verify", "node": "verify", "ok": v["ok"], "issues": v["issues"],
           "numbers_checked": v["numbers_checked"], "attempt": state["attempts"]})
    return {"verification": v}


async def finalize(state: AgentState) -> AgentState:
    u = state["understanding"]
    if u["intent"] == "off_topic":
        text = OFF_TOPIC_REPLY.get(u["language"], OFF_TOPIC_REPLY["en"])
        answer = {"text": text, "language": u["language"], "citations": [], "verified": True,
                  "confidence": "n/a", "warnings": []}
    else:
        v = state["verification"]
        results = state["results"]
        by_id = {r["id"]: r for r in results.values()}
        citations = []
        for c in v["citations_used"]:
            r = by_id[c]
            cite = {k: r[k] for k in ("id", "tool", "source", "url", "fetched_at", "stale")}
            if r["tool"] == "agronomy_docs":  # point at the exact document pages used
                cite["refs"] = [{k: p[k] for k in ("title", "publisher", "year", "page", "url")}
                                for p in r["data"]["passages"]]
            citations.append(cite)
        failed = [r["tool"] for r in results.values() if not r["ok"]]
        stale = [r["tool"] for r in results.values() if r["ok"] and r["stale"]]
        confidence = "high" if v["ok"] and not failed and not stale else "medium" if v["ok"] else "low"
        warnings = list(v["issues"])
        warnings += [f"{t} unavailable" for t in failed] + [f"{t} served from cache" for t in stale]
        answer = {"text": state["draft"], "language": u["language"], "citations": citations,
                  "verified": v["ok"], "confidence": confidence, "warnings": warnings,
                  "attempts": state["attempts"]}
    _emit({"type": "final", "node": "finalize", "answer": answer})
    return {"answer": answer}


# ---------------------------------------------------------------- wiring

def _after_understand(state: AgentState) -> str:
    return "finalize" if state["understanding"]["intent"] == "off_topic" else "run_tools"


def _after_verify(state: AgentState) -> str:
    if state["verification"]["ok"] or state["attempts"] >= MAX_DRAFTS:
        return "finalize"
    return "draft"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("run_tools", run_tools)
    g.add_node("draft", draft)
    g.add_node("verify", verify)
    g.add_node("finalize", finalize)
    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", _after_understand, ["run_tools", "finalize"])
    g.add_edge("run_tools", "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges("verify", _after_verify, ["draft", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build_graph()
