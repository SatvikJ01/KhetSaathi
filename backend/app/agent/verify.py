"""Deterministic answer verifier: every number and citation in the draft must be grounded.

This is the agent's hallucination guardrail. It is plain code, not another LLM call,
so it is fast, cheap, and its verdict is explainable to judges and users.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Zero digit codepoints of Indic scripts -> map their 0-9 to ASCII
_INDIC_ZEROS = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66]
_DIGIT_MAP = {z + i: str(i) for z in _INDIC_ZEROS for i in range(10)}

CITATION_RE = re.compile(r"\[(T\d+)\]")
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:,\d{2,3})*(?:\.\d+)?")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DOSE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(ml|g|gm|kg|मिली|ग्राम|किलो)\s*(/|per|प्रति)\s*(l|litre|liter|acre|ha|लीटर|एकड़|हेक्टेयर)",
    re.IGNORECASE,
)
SMALL_INT_MAX = 10  # counts like "2 days", "3 steps" need no evidence
# Banned in India, or not permitted on vegetables (CIB&RC). Older guides still mention some of these,
# so the answer is rejected even when a cited source contains the name.
BANNED_PESTICIDES = [
    "monocrotophos", "endosulfan", "methyl parathion", "phorate", "carbofuran", "triazophos",
    "dichlorvos", "phosphamidon", "मोनोक्रोटोफॉस", "एंडोसल्फान", "मोनोक्रोटोफोस",
]
DOCS_TOOL = "agronomy_docs"


def normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def _numeric_leaves(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, str):
        for m in DATE_RE.finditer(obj):
            yield from (float(g) for g in m.groups())
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numeric_leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _numeric_leaves(v)


def _numbers_in(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in NUMBER_RE.findall(normalize_digits(text))]


def doc_numbers(results: dict[str, dict[str, Any]]) -> list[float]:
    """Every number written in the retrieved guide passages (doses, years, pages)."""
    docs = results.get(DOCS_TOOL)
    if not docs or not docs["ok"]:
        return []
    return [n for p in docs["data"]["passages"]
            for field in ("text", "year", "title") for n in _numbers_in(str(p[field]))] + \
           [float(p["page"]) for p in docs["data"]["passages"]]


def evidence_numbers(results: dict[str, dict[str, Any]], question: str) -> list[float]:
    nums = [n for r in results.values() if r["ok"] for n in _numeric_leaves(r["data"])]
    return nums + doc_numbers(results) + _numbers_in(question)


def _grounded(value: float, evidence: list[float]) -> bool:
    if value.is_integer() and value <= SMALL_INT_MAX:
        return True
    tol = max(0.6, 0.03 * abs(value))
    return any(abs(value - e) <= tol for e in evidence)


def verify_draft(draft: str, results: dict[str, dict[str, Any]], question: str) -> dict[str, Any]:
    text = normalize_digits(draft)
    issues: list[str] = []
    ok_ids = {r["id"] for r in results.values() if r["ok"]}

    cited = set(CITATION_RE.findall(text))
    bad_cites = sorted(cited - ok_ids)
    if bad_cites:
        issues.append(f"Citation tags {bad_cites} do not refer to successful tool results; use only {sorted(ok_ids)}.")
    if ok_ids and not cited:
        issues.append("The answer cites no evidence; add [T#] tags after each data-based claim.")

    evidence = evidence_numbers(results, question)
    body = CITATION_RE.sub(" ", text)
    ungrounded = []
    for raw in NUMBER_RE.findall(body):
        value = float(raw.replace(",", ""))
        if not _grounded(value, evidence):
            ungrounded.append(raw)
    if ungrounded:
        issues.append(
            f"These numbers are not in the evidence: {sorted(set(ungrounded))}. "
            "Remove them or replace with the exact evidence values."
        )

    docs = results.get(DOCS_TOOL)
    docs_cited = bool(docs and docs["ok"] and docs["id"] in cited)
    # Doses must match numbers in the passage *text*, not page numbers or years.
    doc_nums = {n for p in docs["data"]["passages"] for n in _numbers_in(p["text"])} if docs_cited else set()
    for m in DOSE_RE.finditer(text):
        dose = float(re.match(r"\d+(?:\.\d+)?", m.group(0)).group(0))
        if not docs_cited or dose not in doc_nums:
            issues.append(f"Dose '{m.group(0)}' is not copied from a cited official guide passage. "
                          "Remove it and refer the farmer to the KVK.")

    # LLMs occasionally leak Cyrillic/Greek letters into transliterated chemical names
    if re.search(r"[Ͱ-ϿЀ-ӿ]", text):
        issues.append("Answer contains stray Cyrillic/Greek characters; rewrite those words in the answer's script.")

    lowered = text.lower()
    banned = [b for b in BANNED_PESTICIDES if b in lowered]
    if banned:
        issues.append(f"Banned/restricted pesticide mentioned: {banned}. Never recommend it; remove it.")

    return {
        "ok": not issues,
        "issues": issues,
        "citations_used": sorted(cited & ok_ids, key=lambda c: int(c[1:])),
        "numbers_checked": len(NUMBER_RE.findall(body)),
    }
