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


def evidence_numbers(results: dict[str, dict[str, Any]], question: str) -> list[float]:
    nums = [n for r in results.values() if r["ok"] for n in _numeric_leaves(r["data"])]
    nums += [float(m.replace(",", "")) for m in NUMBER_RE.findall(normalize_digits(question))]
    return nums


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

    if DOSE_RE.search(text):
        issues.append("Chemical dosage given without an official source. Refer the farmer to the KVK instead.")

    return {
        "ok": not issues,
        "issues": issues,
        "citations_used": sorted(cited & ok_ids, key=lambda c: int(c[1:])),
        "numbers_checked": len(NUMBER_RE.findall(body)),
    }
