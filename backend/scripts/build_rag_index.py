"""Build the agronomy RAG index from official documents listed in data/rag/sources.json.

Downloads each PDF, extracts text page by page (poppler `pdftotext`), splits pages into
overlapping chunks, embeds them with Gemini, and writes:
    data/rag/chunks.json       chunk text + source/page metadata
    data/rag/embeddings.npy    float16 matrix aligned with chunks.json

Usage (from backend/):  .venv/bin/python -m scripts.build_rag_index
Requires poppler-utils (pdftotext) and GOOGLE_API_KEY in .env.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
import numpy as np

from app.rag import CHUNKS_PATH, EMB_PATH, EMBED_MODEL, SOURCES_PATH, embedder

CHUNK_CHARS = 650
MIN_CHUNK_CHARS = 120
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def page_texts(pdf: Path) -> list[str]:
    """One string per page, in reading order (not -layout, which interleaves columns)."""
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
                         check=True, capture_output=True, text=True).stdout
    return out.split("\f")


def clean(text: str) -> str:
    text = re.sub(r"-\n(\w)", r"\1", text)  # re-join hyphenated line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def chunk_page(text: str) -> list[str]:
    """Sentence-packed chunks of ~CHUNK_CHARS, overlapping by one sentence.

    Magazine PDFs have few paragraph breaks, so paragraph-based chunks came out
    200-2900 chars and buried specific advice (doses, pests) in long chunks.
    """
    sentences = [s.strip() for s in SENTENCE_RE.split(text.replace("\n", " ")) if s.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    for s in sentences:
        if cur and sum(len(x) + 1 for x in cur) + len(s) > CHUNK_CHARS:
            chunks.append(" ".join(cur))
            cur = [cur[-1]]  # one-sentence overlap keeps context across the boundary
        cur.append(s)
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def main() -> None:
    sources = json.loads(SOURCES_PATH.read_text())
    chunks: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for src in sources:
            pdf = Path(tmp) / f"{src['id']}.pdf"
            resp = httpx.get(src["pdf_url"], follow_redirects=True, timeout=120,
                             headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                raise RuntimeError(f"{src['id']}: response is not a PDF")
            pdf.write_bytes(resp.content)
            pages = page_texts(pdf)
            n_before = len(chunks)
            for page_no, page in enumerate(pages, start=1):
                for text in chunk_page(clean(page)):
                    chunks.append({"source_id": src["id"], "page": page_no, "text": text})
            print(f"{src['id']}: {len(pages)} pages -> {len(chunks) - n_before} chunks")

    vectors = embedder().embed_documents([c["text"] for c in chunks])
    CHUNKS_PATH.write_text(json.dumps({"embed_model": EMBED_MODEL, "chunks": chunks}, ensure_ascii=False, indent=1))
    np.save(EMB_PATH, np.asarray(vectors, dtype=np.float16))
    print(f"wrote {len(chunks)} chunks, embeddings {np.asarray(vectors).shape}")


if __name__ == "__main__":
    main()
