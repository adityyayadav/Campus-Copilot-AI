"""LLM access.

Primary: Groq (gpt-oss-120b for SQL/synthesis, gpt-oss-20b for routing).
Fallback chain on rate limit / outage: other Groq model -> Gemini (OpenAI-compatible endpoint, same key as embeddings).
Every call is metered into a per-request usage counter (calls, tokens, latency) for cost reporting.
"""
import contextvars
import json
import re
import time
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from . import config

# ------------------------------------------------------------------ per-request usage metering
_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)


def start_metering() -> dict:
    u = {"llm_calls": 0, "tokens": 0, "llm_ms": 0, "models": {}}
    _usage.set(u)
    return u


def _record(model: str, tokens: int | None, ms: int):
    u = _usage.get()
    if u is None:
        return
    u["llm_calls"] += 1
    u["tokens"] += tokens or 0
    u["llm_ms"] += ms
    u["models"][model] = u["models"].get(model, 0) + 1


# ------------------------------------------------------------------ clients
def _groq(model: str, json_mode: bool, effort: str):
    kwargs = dict(model=model, temperature=0.0, api_key=config.GROQ_API_KEY, max_retries=1, timeout=45)
    if model.startswith("openai/gpt-oss") and "reasoning_effort" in getattr(ChatGroq, "model_fields", {}):
        kwargs["reasoning_effort"] = effort
    llm = ChatGroq(**kwargs)
    return llm.bind(response_format={"type": "json_object"}) if json_mode else llm


def _gemini(json_mode: bool):
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=config.GEMINI_CHAT_MODEL, temperature=0.0, api_key=config.GOOGLE_API_KEY,
                     base_url="https://generativelanguage.googleapis.com/v1beta/openai/", max_retries=1, timeout=45)
    return llm.bind(response_format={"type": "json_object"}) if json_mode else llm


@lru_cache(maxsize=16)
def get_llm(role: str = "main", json_mode: bool = False):
    """role 'main' = SQL generation (large model, medium reasoning); 'synth' = answer writing (large model, low
    reasoning: the evidence is already gathered); 'router' = routing, HyDE, small talk (small model)."""
    primary = config.ROUTER_MODEL if role == "router" else config.MAIN_MODEL
    effort = "medium" if role == "main" else "low"
    chain = []
    if config.GROQ_API_KEY:
        chain.append(_groq(primary, json_mode, effort))
        chain += [_groq(m, json_mode, "low") for m in dict.fromkeys([config.FALLBACK_MODEL, config.MAIN_MODEL,
                                                                      config.ROUTER_MODEL]) if m != primary]
    if config.GOOGLE_API_KEY and config.GEMINI_CHAT_MODEL:
        try:
            chain.append(_gemini(json_mode))
        except ImportError:
            pass
    if not chain:
        raise RuntimeError("No LLM configured: set GROQ_API_KEY (and optionally GOOGLE_API_KEY) in .env")
    return chain[0].with_fallbacks(chain[1:]) if len(chain) > 1 else chain[0]


def chat(system: str, user: str, role: str = "main", json_mode: bool = False) -> tuple[str, dict]:
    t = time.time()
    msg = get_llm(role, json_mode).invoke([SystemMessage(content=system), HumanMessage(content=user)])
    usage = getattr(msg, "usage_metadata", None) or {}
    model = (getattr(msg, "response_metadata", {}) or {}).get("model_name", "")
    ms = int((time.time() - t) * 1000)
    _record(model, usage.get("total_tokens"), ms)
    return (msg.content or "").strip(), {"ms": ms, "model": model, "tokens": usage.get("total_tokens")}


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise
