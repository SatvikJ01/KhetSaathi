"""Hybrid retrieval over official agronomy documents (ICAR, state agri universities).

BM25 (exact terms: chemical names, variety names, doses) + dense Gemini embeddings
(meaning, paraphrase, Hindi/Marathi queries), merged with Reciprocal Rank Fusion.
If the embedding API is unavailable, retrieval degrades to BM25 only.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "rag"
SOURCES_PATH = DATA_DIR / "sources.json"
CHUNKS_PATH = DATA_DIR / "chunks.json"
EMB_PATH = DATA_DIR / "embeddings.npy"
EMBED_MODEL = "gemini-embedding-2"
RRF_K = 60
BM25_K1, BM25_B = 1.5, 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = set("the of and to in a is for on be with as by at or are it this that from an which can".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


def embedder():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(model=EMBED_MODEL, google_api_key=get_settings().google_api_key)


class Index:
    def __init__(self) -> None:
        sources = {s["id"]: s for s in json.loads(SOURCES_PATH.read_text())}
        self.chunks: list[dict[str, Any]] = json.loads(CHUNKS_PATH.read_text())["chunks"]
        for c in self.chunks:
            c["source"] = sources[c["source_id"]]
        emb = np.load(EMB_PATH).astype(np.float32)
        self.emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)

        self.docs_tokens = [tokenize(c["text"]) for c in self.chunks]
        self.doc_len = np.array([len(t) for t in self.docs_tokens], dtype=np.float32)
        self.avg_len = float(self.doc_len.mean())
        self.tf = [Counter(t) for t in self.docs_tokens]
        df = Counter(term for toks in self.docs_tokens for term in set(toks))
        n = len(self.chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def bm25(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if f:
                    norm = BM25_K1 * (1 - BM25_B + BM25_B * self.doc_len[i] / self.avg_len)
                    scores[i] += idf * f * (BM25_K1 + 1) / (f + norm)
        return scores

    def dense(self, query: str) -> np.ndarray:
        q = np.asarray(embedder().embed_query(query), dtype=np.float32)
        return self.emb @ (q / np.linalg.norm(q))

    def search(self, query: str, k: int = 4) -> tuple[list[dict[str, Any]], str]:
        rankings = []
        bm = self.bm25(query)
        if bm.max() > 0:
            rankings.append(np.argsort(-bm))
        mode = "bm25"
        try:
            rankings.append(np.argsort(-self.dense(query)))
            mode = "hybrid (bm25 + embeddings, RRF)"
        except Exception:
            pass  # embedding API down: keyword retrieval still works
        if not rankings:
            return [], mode
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking[:50]):
                fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(fused, key=fused.get, reverse=True)[:k]
        hits = []
        for idx in top:
            c = self.chunks[idx]
            src = c["source"]
            hits.append({
                "title": src["title"], "publisher": src["publisher"], "year": src["year"],
                "page": c["page"], "url": f"{src['pdf_url']}#page={c['page']}",
                "score": round(fused[idx], 4), "text": c["text"],
            })
        return hits, mode


@lru_cache
def get_index() -> Index:
    return Index()
