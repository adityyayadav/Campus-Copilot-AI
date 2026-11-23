"""Central configuration, loaded from .env."""
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    # strip inline comments like "value   # comment"
    return os.getenv(name, default).split(" #")[0].strip()


GROQ_API_KEY = _get("GROQ_API_KEY")
MAIN_MODEL = _get("MAIN_MODEL", "openai/gpt-oss-120b")
ROUTER_MODEL = _get("ROUTER_MODEL", "openai/gpt-oss-20b")
FALLBACK_MODEL = _get("FALLBACK_MODEL", "openai/gpt-oss-20b")

GEMINI_CHAT_MODEL = _get("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
GUARD_MODEL = _get("GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m")

EMBED_PROVIDER = _get("EMBED_PROVIDER", "gemini").lower()
RERANK_PROVIDER = _get("RERANK_PROVIDER", "cohere").lower()
GOOGLE_API_KEY = _get("GOOGLE_API_KEY")
COHERE_API_KEY = _get("COHERE_API_KEY")
JINA_API_KEY = _get("JINA_API_KEY")

EMBED_DIMS = {"gemini": 768, "cohere": 1024, "jina": 1024}
EMBED_DIM = EMBED_DIMS.get(EMBED_PROVIDER, 768)

DATABASE_URL = _get("DATABASE_URL", "postgresql://copilot_admin:copilot_admin_pw@localhost:5433/campus")
READER_USER = _get("READER_USER", "copilot_reader")
READER_PASSWORD = _get("READER_PASSWORD", "reader_pw_change_me")


def reader_url() -> str:
    """Same host/db as DATABASE_URL but logged in as the read-only role."""
    u = urlparse(DATABASE_URL)
    host = u.hostname or "localhost"
    netloc = f"{READER_USER}:{READER_PASSWORD}@{host}" + (f":{u.port}" if u.port else "")
    return urlunparse(u._replace(netloc=netloc))


DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"

# Retrieval knobs
DENSE_K = 20
BM25_K = 20
RRF_K = 60
RERANK_TOP_N = 5
RERANK_MIN_SCORE = 0.05
SQL_MAX_ATTEMPTS = 3
SQL_ROW_LIMIT = 50

# Routing: tier-1 embedding classifier is trusted above this confidence, otherwise the LLM router decides.
ROUTER_FAST_CONFIDENCE = float(_get("ROUTER_FAST_CONFIDENCE", "0.75"))
ROUTER_LLM_MIN_CONFIDENCE = float(_get("ROUTER_LLM_MIN_CONFIDENCE", "0.55"))  # below -> HYBRID fallback

# Guardrails
MAX_QUESTION_CHARS = 1000
INJECTION_THRESHOLD = float(_get("INJECTION_THRESHOLD", "0.80"))
RATE_LIMIT_PER_MIN = int(_get("RATE_LIMIT_PER_MIN", "20"))

# Semantic cache
CACHE_SIMILARITY = float(_get("CACHE_SIMILARITY", "0.95"))
CACHE_TTL_DOCS_S = 24 * 3600     # policy answers change rarely
CACHE_TTL_LIVE_S = 5 * 60        # anything touching live SQL data
