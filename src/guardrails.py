"""Guardrails, applied in layers (defence in depth):

1. Input validation    length, control characters, empty / garbage input
2. Prompt injection    regex heuristics (free, instant) + Llama Prompt Guard 2 classifier hosted on Groq
3. Authorization       a student may only ask about their own records (other student IDs are refused);
                       enforced again in SQL (sensitive-column check) and in Postgres (row-level security)
4. Untrusted documents chunks are scanned at ingest; flagged chunks are excluded, and retrieved text is
                       wrapped in <document> tags that the LLM is told to treat as data, never instructions
"""
import re
import time
import unicodedata
from collections import defaultdict, deque

import requests

from . import config

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"(reveal|print|show|repeat|leak).{0,30}(system prompt|instructions|hidden prompt)",
    r"you are now (a|an|in) ",
    r"\b(jailbreak|DAN mode|developer mode)\b",
    r"act as (an? )?(admin|administrator|root|dba|superuser)",
    r"\b(drop|truncate|alter|delete from|insert into|update \w+ set)\b.{0,40}\b(table|database|schema|role|students|fee_ledger)\b",
    r";\s*--",
    r"</?(system|assistant|instructions?)>",
]
_INJ_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)
_STUDENT_ID = re.compile(r"\bS\d{4}\b", re.I)


class GuardrailViolation(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


def sanitize(question: str) -> str:
    q = unicodedata.normalize("NFKC", question or "")
    q = "".join(ch for ch in q if ch in "\n\t" or unicodedata.category(ch)[0] != "C")  # strip control/zero-width
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 2:
        raise GuardrailViolation("invalid_input", "Please type a question.")
    if len(q) > config.MAX_QUESTION_CHARS:
        raise GuardrailViolation("invalid_input", f"Questions are limited to {config.MAX_QUESTION_CHARS} characters.")
    if sum(ch.isalnum() for ch in q) < 2:
        raise GuardrailViolation("invalid_input", "That doesn't look like a question.")
    return q


def heuristic_injection(text: str) -> str | None:
    m = _INJ_RE.search(text)
    return m.group(0) if m else None


def prompt_guard_score(text: str) -> float | None:
    """Probability (0-1) that the text is a prompt-injection/jailbreak, from Llama Prompt Guard 2 on Groq.
    Returns None if the classifier is unavailable (we then rely on heuristics only)."""
    if not config.GROQ_API_KEY or not config.GUARD_MODEL:
        return None
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                          json={"model": config.GUARD_MODEL, "messages": [{"role": "user", "content": text[:2000]}]},
                          timeout=8)
        if not r.ok:
            return None
        return float(r.json()["choices"][0]["message"]["content"].strip())
    except Exception:
        return None


def check_injection(text: str) -> dict:
    hit = heuristic_injection(text)
    score = prompt_guard_score(text)
    blocked = bool(hit) or (score is not None and score >= config.INJECTION_THRESHOLD)
    result = {"heuristic_match": hit, "classifier_score": None if score is None else round(score, 4), "blocked": blocked}
    if blocked:
        raise GuardrailViolation("prompt_injection",
                                 "This request looks like an attempt to override the assistant's instructions, "
                                 "so it was not processed. Please rephrase your question.", result)
    return result


def check_authorization(text: str, profile: dict | None) -> None:
    if (profile or {}).get("role") == "admin":
        return  # administrators may look up any student
    mentioned = {m.upper() for m in _STUDENT_ID.findall(text)}
    own = (profile or {}).get("student_id")
    others = mentioned - ({own} if own else set())
    if others:
        raise GuardrailViolation("unauthorized",
                                 "You can only access your own student records. "
                                 f"Requests about {', '.join(sorted(others))} are not permitted.",
                                 {"requested": sorted(others), "signed_in_as": own})


def wrap_untrusted(label: str, title: str, text: str) -> str:
    text = text.replace("<document", "&lt;document").replace("</document", "&lt;/document")
    return f'<document id="{label}" title="{title}">\n{text}\n</document>'


# ------------------------------------------------------------------ rate limiting (per client, sliding window)
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(client_id: str) -> None:
    now = time.time()
    q = _hits[client_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.RATE_LIMIT_PER_MIN:
        raise GuardrailViolation("rate_limited", "Too many requests. Please wait a moment and try again.")
    q.append(now)
