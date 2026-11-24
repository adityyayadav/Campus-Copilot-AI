# 🎓 Campus Copilot AI

**Ask one question about your university and get one clear, verified answer.**

University information lives in two places: **records** (fee ledgers, courses, grants, exam schedules) and **policy
documents** (rules, eligibility criteria, fines). Students usually have to check both and put the answer together
themselves. Campus Copilot does that for them. It works out where the answer lives, looks it up, combines the two and
shows exactly where every fact came from.

🎥 **Demo video:** _add link here_
💻 **Code:** https://github.com/adityyayadav/Campus-Copilot-AI
📊 **Slides:** [presentation/Campus-Copilot-AI.pptx](presentation/Campus-Copilot-AI.pptx)

---

## What it can do

- **Answers from records:** "How many seats are left in the Generative AI elective?"
- **Answers from policies:** "What is the refund if I withdraw 20 days after the term starts?"
- **Combines both:** "I'm a 3rd-year CSE student with GPA 3.8. Which research grants can I apply for, and by when?"
- **Personal answers:** a signed-in student can ask "What fees do I still owe?" and sees only their own records.
- **Shows its sources:** every answer has numbered citations. Clicking one opens the original document, or the PDF page
  with the exact lines highlighted.
- **Checks its numbers:** every figure in an answer is checked against the source before it is shown.
- **Stays on topic:** it only answers university and student questions, and politely declines anything else (poems,
  jokes, general trivia).
- **Stays safe:** it blocks prompt-injection attempts and never shows one student's private data to another.

## How it works (in short)

1. **Understand the question.** A two-step router decides whether it needs the database, the documents, or both.
2. **Look it up.** It writes a safe, read-only database query and/or searches the policy documents.
3. **Write the answer.** An AI model combines the evidence into a short answer with citations.
4. **Verify.** Any number that can't be found in the evidence is fixed before the answer is sent.

Repeated questions are answered instantly from a cache. If an AI service hits its free-tier limit, the app switches to a
backup model or to keyword search, so it keeps answering.

**Built with:** React · FastAPI · LangGraph · PostgreSQL + pgvector · Groq and Gemini (free-tier AI models) · Cohere Rerank

## Run it locally

You need **Python 3.11+**, **Node 18+** and **Docker Desktop** (running), plus free API keys from Groq, Google AI Studio
and Cohere.

1. Copy `.env.example` to `.env` and fill in `GROQ_API_KEY`, `GOOGLE_API_KEY` and `COHERE_API_KEY`.
2. Set it up once:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   docker compose up -d
   .\.venv\Scripts\python.exe -m scripts.setup_db
   .\.venv\Scripts\python.exe -m scripts.ingest
   cd web; npm install; npm run build; cd ..
   ```

3. Start the app and open **http://localhost:8000**:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn src.api:app --port 8000
   ```

If you change `.env`, restart the server.

## Demo accounts

Switch between them from the account menu in the app.

| Account | What they can see |
|---|---|
| Registrar Office (admin) | Everything, including any student's records |
| Aarav Sharma (S1023), Priya Kulkarni (S1045) | Public information plus **their own** records |
| Guest | Public information only |

Access is enforced inside the database itself, so even a tricked AI model can't read another student's data.

## Adding your own documents

Put Markdown or PDF policy files in `data/docs/`, then run
`.\.venv\Scripts\python.exe -m scripts.ingest` again.

## Project layout

```
src/            backend: API, router, search, database agent, answer checks
web/            the chat app (React)
data/           sample university data and policy documents
scripts/        database setup and document ingestion
eval/           routing accuracy tests
docs/           architecture, routing evaluation, demo script, prompt log
presentation/   final slides
```

## Learn more

- [Architecture](docs/ARCHITECTURE.md): how each part works, in detail
- [Routing evaluation](docs/ROUTING_EVALUATION.md): how accurate the router is (94%)
- [Demo script](docs/DEMO_SCRIPT.md): questions that show every feature
- [Prompt log](docs/PROMPT_LOG.md): how the project was built with AI assistance

## Good to know

- API keys live only in `.env`, which is never committed.
- The free tiers have daily limits. Before a demo, avoid long test runs and ask your demo questions once, so they are
  cached and answer instantly.
