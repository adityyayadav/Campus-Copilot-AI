"""Hosted embedding + reranking providers (no local models).

Embeddings: gemini | cohere | jina      Reranker: cohere | jina | none
All are called over plain HTTPS so there are no heavy SDK dependencies.
"""
import hashlib
import re
import time
from collections import OrderedDict

import requests

from . import config

_TIMEOUT = 60


class QuotaExhausted(RuntimeError):
    """A provider quota that will not reset within a request's lifetime (e.g. a daily limit)."""
    def __init__(self, url: str, retry_after_s: float, detail: str):
        super().__init__(f"{url} -> quota exhausted, resets in ~{int(retry_after_s)}s: {detail[:160]}")
        self.retry_after_s = retry_after_s


_MAX_WAIT_S = 70  # longer than this means a daily/long quota: retrying inside a request is pointless


def _post(url: str, headers: dict, payload: dict, retries: int = 5) -> dict:
    for attempt in range(retries):
        r = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            hint = re.search(r"retry in ([\d.]+)s", r.text) or re.search(r'"retryDelay":\s*"([\d.]+)s"', r.text)
            wait = float(r.headers.get("retry-after") or (hint.group(1) if hint else 2 ** attempt * 3))
            if r.status_code == 429 and (wait > _MAX_WAIT_S or "PerDay" in r.text):
                raise QuotaExhausted(url, max(wait, 3600.0), r.text)
            time.sleep(min(wait + 1, 65))
            continue
        if not r.ok:
            raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError(f"{url} -> still rate limited after {retries} retries")


# ----------------------------------------------------------------- embeddings
def _gemini_embed(texts: list[str], is_query: bool) -> list[list[float]]:
    model = "models/gemini-embedding-001"
    # Free tier: 100 embedded texts per minute (each batch item counts) -> batches of 90, paced
    out = []
    for i in range(0, len(texts), 90):
        if i:
            time.sleep(61)
        batch = texts[i:i + 90]
        payload = {"requests": [{
            "model": model,
            "content": {"parts": [{"text": t}]},
            "taskType": "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT",
            "outputDimensionality": config.EMBED_DIM,
        } for t in batch]}
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/{model}:batchEmbedContents",
                     {"x-goog-api-key": config.GOOGLE_API_KEY, "Content-Type": "application/json"}, payload)
        out.extend(e["values"] for e in data["embeddings"])
    return [_normalize(v) for v in out]  # truncated Gemini vectors must be re-normalised


def _cohere_embed(texts: list[str], is_query: bool) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), 96):
        data = _post("https://api.cohere.com/v2/embed",
                     {"Authorization": f"Bearer {config.COHERE_API_KEY}"},
                     {"model": "embed-english-v3.0", "texts": texts[i:i + 96],
                      "input_type": "search_query" if is_query else "search_document",
                      "embedding_types": ["float"]})
        out.extend(data["embeddings"]["float"])
    return out


def _jina_embed(texts: list[str], is_query: bool) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), 64):
        data = _post("https://api.jina.ai/v1/embeddings",
                     {"Authorization": f"Bearer {config.JINA_API_KEY}"},
                     {"model": "jina-embeddings-v3", "input": texts[i:i + 64],
                      "task": "retrieval.query" if is_query else "retrieval.passage",
                      "dimensions": config.EMBED_DIM})
        out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
    return out


def _normalize(v: list[float]) -> list[float]:
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


_EMBEDDERS = {"gemini": _gemini_embed, "cohere": _cohere_embed, "jina": _jina_embed}


# Circuit breaker: after a quota failure, skip embedding calls until the quota resets so every question
# degrades straight to keyword (BM25) retrieval and the LLM router instead of hanging on retries.
_EMBED_DOWN = {"until": 0.0, "reason": ""}


def embeddings_available() -> bool:
    configured = bool({"gemini": config.GOOGLE_API_KEY, "cohere": config.COHERE_API_KEY,
                       "jina": config.JINA_API_KEY}.get(config.EMBED_PROVIDER))
    return configured and time.time() >= _EMBED_DOWN["until"]


def embeddings_status() -> dict:
    down = time.time() < _EMBED_DOWN["until"]
    return {"available": embeddings_available(), "degraded": down,
            "retry_in_s": int(_EMBED_DOWN["until"] - time.time()) if down else 0,
            "reason": _EMBED_DOWN["reason"] if down else ""}


def embed_query(text: str) -> list[float] | None:
    """One query vector, or None when embeddings are unavailable (callers fall back to keyword search)."""
    if not embeddings_available():
        return None
    try:
        return embed([text], is_query=True)[0]
    except QuotaExhausted as e:
        _EMBED_DOWN.update(until=time.time() + e.retry_after_s, reason="daily quota exhausted")
    except Exception as e:  # outage / repeated rate limits: back off briefly, keep answering
        _EMBED_DOWN.update(until=time.time() + 120, reason=f"{type(e).__name__}: {str(e)[:120]}")
    return None


_QCACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_QCACHE_MAX = 4096
STATS = {"embed_api_calls": 0, "embed_cache_hits": 0}


def embed(texts: list[str], is_query: bool = False) -> list[list[float]]:
    """Query embeddings are memoised (LRU) so one question is embedded once and reused by the
    router, the semantic cache, document retrieval and schema-card retrieval."""
    if config.EMBED_PROVIDER not in _EMBEDDERS:
        raise ValueError(f"Unknown EMBED_PROVIDER={config.EMBED_PROVIDER}")
    if not is_query:
        STATS["embed_api_calls"] += 1
        return _EMBEDDERS[config.EMBED_PROVIDER](texts, False)
    keys = [hashlib.sha256(f"{config.EMBED_PROVIDER}|{t.strip().lower()}".encode()).hexdigest() for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in _QCACHE]
    STATS["embed_cache_hits"] += len(texts) - len(missing)
    if missing:
        STATS["embed_api_calls"] += 1
        vecs = _EMBEDDERS[config.EMBED_PROVIDER]([texts[i] for i in missing], True)
        for i, v in zip(missing, vecs):
            _QCACHE[keys[i]] = v
            if len(_QCACHE) > _QCACHE_MAX:
                _QCACHE.popitem(last=False)
    for k in keys:
        _QCACHE.move_to_end(k)
    return [_QCACHE[k] for k in keys]


# ----------------------------------------------------------------- reranking
def rerank(query: str, docs: list[str], top_n: int) -> list[tuple[int, float]] | None:
    """Returns [(original_index, relevance_score)] best-first, or None if reranking is off/unavailable."""
    if not docs:
        return []
    p = config.RERANK_PROVIDER
    if p == "cohere" and config.COHERE_API_KEY:
        data = _post("https://api.cohere.com/v2/rerank",
                     {"Authorization": f"Bearer {config.COHERE_API_KEY}"},
                     {"model": "rerank-v3.5", "query": query, "documents": docs, "top_n": top_n})
        return [(r["index"], r["relevance_score"]) for r in data["results"]]
    if p == "jina" and config.JINA_API_KEY:
        data = _post("https://api.jina.ai/v1/rerank",
                     {"Authorization": f"Bearer {config.JINA_API_KEY}"},
                     {"model": "jina-reranker-v2-base-multilingual", "query": query,
                      "documents": docs, "top_n": top_n, "return_documents": False})
        return [(r["index"], r["relevance_score"]) for r in data["results"]]
    return None
