# 🎓 Campus Copilot AI: Unified Hybrid RAG

Ask one question; get one answer that merges the **live university database** (fees, grants, courses, exams) with
**policy documents** (eligibility rules, fines, regulations), with exact figures, citations and a full audit trail.

**Stack:** React (Vite) · FastAPI (SSE streaming) · LangGraph · PostgreSQL 17 + pgvector (relational + vectors + BM25 in one DB)
· Groq `gpt-oss-120b/20b` (Gemini fallback) · Gemini embeddings · Cohere Rerank · Llama Prompt Guard 2.
Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prerequisites
- Python 3.11+ (tested on 3.13) · Node 18+ · Docker Desktop (running)
- Free API keys in `.env` (copy from `.env.example`): `GROQ_API_KEY`, `GOOGLE_API_KEY`, `COHERE_API_KEY`

## First-time setup
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d                                  # PostgreSQL + pgvector on localhost:5433
.\.venv\Scripts\python.exe -m scripts.setup_db        # schema, seed data, read-only role, row-level security
.\.venv\Scripts\python.exe -m scripts.ingest          # injection scan, chunk, embed, BM25 index (~3 min on free tier)
cd web; npm install; npm run build; cd ..             # build the React UI
```

## Run
```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api:app --port 8000
```
- **http://localhost:8000/** is the student-facing assistant: a clean chat UI with conversation history, sources and an account switcher.
- **http://localhost:8000/console** is the developer console: routing decisions, generated SQL, retrieved chunks with scores, guardrail and grounding results, cost, and the Insights dashboard.

For UI development with hot reload, also run `cd web; npm run dev` and open http://localhost:5173.

## Demo accounts (role-based access, enforced in PostgreSQL)
| Account | Can see |
|---|---|
| Registrar Office (admin) | Everything, including any student's fees and applications |
| Aarav Sharma (S1023), Priya Kulkarni (S1045) | Public info plus **their own** records only |
| Guest | Public information only (no personal rows) |

Roles are applied per query through `app.role` / `app.student_id` and Postgres row-level security, plus the guardrail and SQL checks. In production they would come from the university SSO token, not an account switcher.

## Evaluate
```powershell
.\.venv\Scripts\python.exe -m eval.run_eval          # routing: tier-1 vs LLM vs two-tier → eval/results.md
.\.venv\Scripts\python.exe -m eval.run_eval --full   # + SQL success, grounding, latency, tokens
```

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/chat` | `{question, student_id?, history?, use_rerank?, use_hyde?}` → Server-Sent Events: `guard`, `cache`, `step`, `final` / `blocked` / `error` |
| GET | `/api/metrics` | latency p50/p95, cache hit rate, % routed without LLM, tokens, SQL success, guardrail blocks |
| GET | `/api/health` | DB, index size, configured providers |
| GET | `/api/personas` | demo student accounts |

## Notes
- API keys live only in `.env` (git-ignored). The keys used during development were shared in chat, so rotate them before making the repo public.
- Free-tier limits: Gemini embeddings allow 100 texts/min, so ingestion is paced. Groq `gpt-oss-120b` allows 8K tokens/min; on a 429 the app falls back to `gpt-oss-20b`, then to Gemini.
