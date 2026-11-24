"""LangGraph orchestration of the hybrid RAG pipeline.

                     +--> sql -----------------------------------+
  router (2-tier) ---+--> docs ----------------------------------+--> synthesize --> grounding --> END
     SQL_ONLY        +--> docs --(HYBRID sequential)--> sql -----+
     VECTOR_ONLY     +--> sql + docs (HYBRID parallel, same superstep)
     HYBRID          +--> out_of_scope --> END
"""
import datetime as dt
import operator
import re
import time
from functools import lru_cache
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from . import config, prompts
from .guardrails import wrap_untrusted
from .llm import chat
from .retrieval import expand_to_parent_sections, hybrid_search
from .router import route as route_question
from .sql_agent import rows_to_markdown, run_sql_agent


class State(TypedDict, total=False):
    question: str
    history: list[dict]
    profile: dict | None
    use_hyde: bool
    use_rerank: bool
    route: str              # SQL_ONLY | VECTOR_ONLY | HYBRID
    router: dict
    sql_result: dict
    doc_result: dict
    answer: str
    grounding: dict
    trace: Annotated[list, operator.add]


def _profile_str(p: dict | None) -> str:
    return ", ".join(f"{k}={v}" for k, v in p.items() if v not in (None, "")) if p else "not logged in"


def _history_str(h: list[dict] | None) -> str:
    return "\n".join(f"{m['role']}: {m['content'][:400]}" for m in (h or [])[-6:]) or "(none)"


# ------------------------------------------------------------------ nodes
def router_node(state: State) -> dict:
    dec = route_question(state["question"], state.get("profile"), state.get("history"))
    return {"route": dec["route"], "router": dec,
            "trace": [{"node": "router", "ms": dec["ms"], "model": dec.get("model"),
                       "output": {k: dec.get(k) for k in ("route", "strategy", "tier", "confidence", "in_scope")}}]}


def _policy_text(chunks: list[dict], max_chars: int = 2500) -> str:
    parts, n = [], 0
    for i, c in enumerate(chunks, 1):
        block = wrap_untrusted(f"D{i}", f"{c['title']} > {c['section']}", c["content"])
        if n + len(block) > max_chars:
            break
        parts.append(block)
        n += len(block)
    return "\n\n".join(parts)


def docs_node(state: State) -> dict:
    t0 = time.time()
    r = state["router"]
    q = r.get("doc_question") or r["standalone_question"]
    hyde_text, hyde_ms = None, 0
    if state.get("use_hyde"):
        try:
            hyde_text, m = chat(prompts.HYDE_SYSTEM, q, role="router")
            hyde_ms = m["ms"]
        except Exception:
            hyde_text = None
    rerank = state.get("use_rerank", True)
    filters = {"topic": r["doc_topic"]} if r.get("doc_topic") else {}
    res = hybrid_search(q, ["docs", "table_rows"], filters, use_rerank=rerank, dense_query=hyde_text)
    used_filter = bool(filters)
    best = max((c.get("rerank_score") or 0) for c in res["chunks"]) if res["chunks"] else 0
    if filters and (not res["chunks"] or all(c.get("low_relevance") for c in res["chunks"])
                    or (res["reranked"] and best < 0.40)):  # weak hits under the filter -> topic guess was wrong, search all
        res = hybrid_search(q, ["docs", "table_rows"], {}, use_rerank=rerank, dense_query=hyde_text)
        used_filter = False
    if r.get("strategy") == "sequential" and state["route"] == "HYBRID":
        res["chunks"] = expand_to_parent_sections(res["chunks"])  # complete rule sets for SQL translation
    out = {"query": q, "filters": filters if used_filter else {}, "filter_relaxed": bool(filters) and not used_filter,
           "hyde": hyde_text, "chunks": res["chunks"], "reranked": res["reranked"], "timings": res["timings"]}
    return {"doc_result": out,
            "trace": [{"node": "docs", "ms": int((time.time() - t0) * 1000) + hyde_ms,
                       "output": {"n_chunks": len(res["chunks"]), "reranked": res["reranked"], "filters": out["filters"]}}]}


def sql_node(state: State) -> dict:
    r = state["router"]
    q = r.get("sql_question") or r["standalone_question"]
    sequential = state["route"] == "HYBRID" and r.get("strategy") == "sequential"
    policy = _policy_text(state["doc_result"]["chunks"], max_chars=6000) if sequential and state.get("doc_result") else ""
    res = run_sql_agent(q, state.get("profile"), policy, r["standalone_question"])
    res.update(question=q, used_policy=bool(policy))
    return {"sql_result": res,
            "trace": [{"node": "sql", "ms": res["ms"],
                       "output": {"tables": res["tables"], "attempts": res["attempts"], "rows": len(res["rows"]),
                                  "error": res["error"], "used_policy_rules": bool(policy)}}]}


def _sql_block(s: dict | None) -> str:
    if not s:
        return "(database not queried for this question)"
    if s.get("error"):
        return f"(database query failed: {s['error']})"
    note = (f"\n(Result limited to the first {len(s['rows'])} rows; tell the user the list is truncated and offer to"
            " narrow it down, e.g. by year.)" if len(s["rows"]) >= config.SQL_ROW_LIMIT else "")
    applied = ("\n(These rows were ALREADY FILTERED by the policy rules shown in the excerpts: every row returned satisfies"
               " them. Present them as the qualifying records; do not refuse because a rule column is not displayed.)"
               if s.get("used_policy") else "")
    return f"Query intent: {s.get('explanation', '')}{applied}\n{rows_to_markdown(s['columns'], s['rows'])}{note}"


def _doc_block(d: dict | None) -> str:
    if not d:
        return "(documents not searched for this question)"
    if not d["chunks"]:
        return "(no relevant policy text found)"
    return _policy_text(d["chunks"], max_chars=6000)


def synthesize_node(state: State) -> dict:
    r = state["router"]
    system = prompts.SYNTH_SYSTEM.format(today=dt.date.today().strftime("%d %B %Y"))
    user = prompts.SYNTH_USER.format(question=r["standalone_question"], profile=_profile_str(state.get("profile")),
                                     sql_block=_sql_block(state.get("sql_result")),
                                     doc_block=_doc_block(state.get("doc_result")))
    answer, meta = chat(system, user, role="synth")
    answer = answer.replace("【", "[").replace("】", "]")
    return {"answer": answer, "trace": [{"node": "synthesize", "ms": meta["ms"], "model": meta.get("model"),
                                         "tokens": meta.get("tokens")}]}


_EXAMPLES = ("You could ask, for example:\n"
             "- *What is the refund if I withdraw 20 days after the term starts?*\n"
             "- *Which research grants are open right now?*\n"
             "- *What is the minimum attendance required to sit for exams?*")
_GREETING = re.compile(r"^\s*(hi|hello|hey|hii+|good (morning|afternoon|evening)|namaste|yo)\b[\s!.,]*"
                       r"(there|copilot)?[\s!.,]*$|\b(who are you|what can you (do|help)|how can you help|help me)\b", re.I)
_THANKS = re.compile(r"^\s*(thanks|thank you|thx|ty|great|ok(ay)?|cool|got it|nice)\b[\s\w!.,]{0,30}$", re.I)
_GREETING_REPLY = ("Hi! I'm Campus Copilot. I answer questions about the university: fees and fines, courses and "
                   "timetables, exams, research grants, scholarships, placements, hostel rules and academic policies.\n\n"
                   + _EXAMPLES)
_THANKS_REPLY = "You're welcome! Ask me anything else about your courses, fees, exams or university policies."
_DECLINE_REPLY = ("Sorry, I can only help with student and university matters: fees and fines, courses and "
                  "timetables, exams, research grants, scholarships, placements, hostel rules and academic policies. "
                  "If you meant a university question, try naming the course, department or policy.\n\n" + _EXAMPLES)


def out_of_scope_node(state: State) -> dict:
    """Fixed replies, no LLM call: the assistant never does non-university tasks (poems, code, trivia...)."""
    q = state["question"]
    if _THANKS.search(q):
        answer, kind = _THANKS_REPLY, "thanks"
    elif _GREETING.search(q):
        answer, kind = _GREETING_REPLY, "greeting"
    else:
        answer, kind = _DECLINE_REPLY, "declined"
    return {"answer": answer, "grounding": {"status": "n/a"},
            "trace": [{"node": "out_of_scope", "ms": 0, "model": None, "output": {"reply": kind}}]}


# ------------------------------------------------------------------ grounding (Self-RAG style verification)
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_CITE = re.compile(r"\[(?:SQL|D\d+)\]")


def _norm(x: str) -> str | None:
    try:
        v = float(x.replace(",", ""))
    except ValueError:
        return None
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _evidence_numbers(state: State) -> set[str]:
    texts = [state["question"], _profile_str(state.get("profile")), dt.date.today().isoformat()]
    s = state.get("sql_result")
    if s:
        texts += [str(v) for row in s.get("rows", []) for v in row.values()]
    d = state.get("doc_result")
    if d:
        texts += [c["content"] for c in d.get("chunks", [])]
    nums = set()
    for t in texts:
        for tok in _NUM.findall(t.replace("-", " ").replace(":", " ")):
            if (n := _norm(tok)) is not None:
                nums.add(n)
    return nums


def unsupported_numbers(answer: str, state: State) -> list[str]:
    ev = _evidence_numbers(state)
    bad = []
    for tok in _NUM.findall(_CITE.sub("", answer).replace("-", " ").replace(":", " ")):
        n = _norm(tok)
        if n is None or float(n) <= 10 or n in ev:
            continue
        if tok not in bad:
            bad.append(tok)
    return bad


def grounding_node(state: State) -> dict:
    t0 = time.time()
    answer = state["answer"]
    bad = unsupported_numbers(answer, state)
    result = {"status": "grounded", "checked_numbers": len(_NUM.findall(_CITE.sub("", answer))), "unsupported": bad}
    if bad:
        try:
            revised, _ = chat(prompts.SYNTH_SYSTEM.format(today=dt.date.today().strftime("%d %B %Y")),
                              prompts.GROUNDING_REVISE_USER.format(answer=answer, unsupported=", ".join(bad))
                              + "\n\nEvidence:\n" + _sql_block(state.get("sql_result")) + "\n\n" + _doc_block(state.get("doc_result")),
                              role="synth")
            answer = revised.replace("【", "[").replace("】", "]")
            result.update(status="revised", derived_after_revision=unsupported_numbers(answer, state))
        except Exception as e:  # noqa: BLE001  a failed revision must not lose a good-enough answer
            result.update(status="unverified", revision_error=type(e).__name__)
    return {"answer": answer, "grounding": result,
            "trace": [{"node": "grounding", "ms": int((time.time() - t0) * 1000), "output": result}]}


# ------------------------------------------------------------------ wiring
def _after_router(state: State):
    r = state["router"]
    if not r.get("in_scope", True):
        return ["out_of_scope"]
    if state["route"] == "SQL_ONLY":
        return ["sql"]
    if state["route"] == "VECTOR_ONLY":
        return ["docs"]
    return ["docs"] if r.get("strategy") == "sequential" else ["sql", "docs"]


def _after_docs(state: State):
    r = state["router"]
    return "sql" if state["route"] == "HYBRID" and r.get("strategy") == "sequential" else "synthesize"


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(State)
    g.add_node("router", router_node)
    g.add_node("docs", docs_node)
    g.add_node("sql", sql_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("grounding", grounding_node)
    g.add_node("out_of_scope", out_of_scope_node)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", _after_router, ["sql", "docs", "out_of_scope"])
    g.add_conditional_edges("docs", _after_docs, ["sql", "synthesize"])
    g.add_edge("sql", "synthesize")
    g.add_edge("synthesize", "grounding")
    g.add_edge("grounding", END)
    g.add_edge("out_of_scope", END)
    return g.compile()
