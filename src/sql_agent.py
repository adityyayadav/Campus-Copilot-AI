"""Text-to-SQL agent: schema-card retrieval -> generate -> guard (sqlglot) -> execute (read-only role) -> self-repair."""
import datetime as dt
import decimal
import re
import time

import sqlglot
from sqlglot import exp

from . import config, prompts
from .db import admin_conn, run_readonly_query
from .llm import chat, parse_json
from .retrieval import hybrid_search

_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in
                         ["Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command",
                          "Merge", "TruncateTable", "Grant", "Into"] if hasattr(exp, n))
_FORBIDDEN_FUNCS = re.compile(r"\b(pg_\w+|dblink\w*|lo_\w+|set_config|current_setting|copy)\s*\(", re.I)


class UnsafeSQL(Exception):
    pass


def all_tables() -> list[str]:
    with admin_conn() as conn:
        return [r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")]


def guard_sql(sql: str, allowed: set[str], own_student_id: str | None = None, limit: int = config.SQL_ROW_LIMIT,
              is_admin: bool = False) -> str:
    """Static safety + validity checks. Returns the normalised SQL that will be executed."""
    try:
        stmts = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"SQL parse error: {str(e)[:200]}")
    if len(stmts) != 1:
        raise UnsafeSQL("Exactly one statement is allowed.")
    stmt = stmts[0]
    if not isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise UnsafeSQL(f"Only SELECT queries are allowed (got {type(stmt).__name__}).")
    if stmt.find(*_FORBIDDEN_NODES):
        raise UnsafeSQL("Query contains a data-modifying clause.")
    # "--" comments silently swallow the rest of a line: on a one-line query that drops every following condition
    if "--" in re.sub(r"'(?:[^']|'')*'", "''", sql):
        raise UnsafeSQL("Line comments (--) are not allowed because they can hide conditions; use /* ... */ comments.")
    if _FORBIDDEN_FUNCS.search(sql):
        raise UnsafeSQL("Query uses a forbidden system function.")
    ctes = {c.alias_or_name for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        if t.db and t.db.lower() != "public":
            raise UnsafeSQL(f"Schema '{t.db}' is not accessible.")
        if t.name and t.name not in allowed and t.name not in ctes:
            raise UnsafeSQL(f"Unknown table '{t.name}'. Available: {', '.join(sorted(allowed))}")
    if not is_admin:  # administrators may read individual records
        _check_personal_columns(stmt, own_student_id)
    if isinstance(stmt, exp.Select) and not stmt.args.get("limit"):
        stmt = stmt.limit(limit)
    return stmt.sql(dialect="postgres", pretty=True)


SENSITIVE_COLUMNS = {"gpa", "attendance_pct", "disciplinary_flag", "grade"}


def _check_personal_columns(stmt: exp.Expression, own: str | None) -> None:
    """Individual-level academic data (GPA, attendance, grades, disciplinary flag) may be *returned* only for the
    signed-in student. Using these columns in filters or aggregates (AVG, COUNT ...) is allowed."""
    for select in stmt.find_all(exp.Select):
        exposed = []
        for proj in select.expressions:
            for col in proj.find_all(exp.Column):
                if col.name.lower() in SENSITIVE_COLUMNS and not col.find_ancestor(exp.AggFunc):
                    exposed.append(col.name)
            stars = [s for s in ([proj] if isinstance(proj, exp.Star) else list(proj.find_all(exp.Star)))
                     if not s.find_ancestor(exp.AggFunc)]
            if stars:
                tables = {t.name for t in select.find_all(exp.Table)}
                if tables & {"students", "enrollments"}:
                    exposed.append("*")
        if not exposed:
            continue
        where = select.args.get("where")
        scoped = False
        if where and own:
            for eq in where.find_all(exp.EQ):
                cols = [c.name.lower() for c in eq.find_all(exp.Column)]
                lits = [l.this for l in eq.find_all(exp.Literal) if l.is_string]
                if "student_id" in cols and own in lits:
                    scoped = True
        if not scoped:
            raise UnsafeSQL(f"Returning individual {', '.join(sorted(set(exposed)))} is only allowed for the signed-in "
                            f"student (add WHERE student_id = '{own}')" if own else
                            "Individual academic data is only available to a signed-in student; use aggregates instead.")


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v) if v != v.to_integral_value() else int(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return v


def add_date_math(cols: list[str], rows: list[dict]) -> tuple[list[str], list[dict]]:
    """LLMs miscount days between dates, so day counts are computed here exactly:
    due_date -> days_overdue (0 if not yet due), deadline / exam_date -> days_left."""
    today = dt.date.today()
    plan = [(src, dst) for src, dst in (("due_date", "days_overdue"), ("deadline", "days_left"), ("exam_date", "days_left"))
            if src in cols and dst not in cols]
    if not plan or not rows:
        return cols, rows
    for r in rows:
        for src, dst in plan:
            try:
                d = dt.date.fromisoformat(str(r.get(src))[:10])
            except ValueError:
                continue
            r[dst] = max((today - d).days, 0) if dst == "days_overdue" else (d - today).days
    return cols + [dst for _, dst in plan if dst not in cols], rows


def rows_to_markdown(cols: list[str], rows: list[dict], max_rows: int = 50) -> str:
    if not rows:
        return "(query returned 0 rows)"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    body = "\n".join("| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows[:max_rows])
    more = f"\n(+{len(rows) - max_rows} more rows)" if len(rows) > max_rows else ""
    return f"{head}\n{body}{more}"


def select_schema(question: str) -> tuple[list[str], dict[str, str]]:
    """Retrieve the most relevant schema cards, then add one hop of FK-referenced tables."""
    hits = hybrid_search(question, ["schema_cards"], top_n=4, use_rerank=False)["chunks"]
    picked = [h["metadata"]["table"] for h in hits]
    with admin_conn() as conn:
        cards = {r["t"]: r["content"] for r in conn.execute(
            "SELECT metadata->>'table' AS t, content FROM rag.chunks WHERE collection='schema_cards'")}
    for t in list(picked):
        for ref in re.findall(r"FK -> (\w+)\.", cards.get(t, "")):
            if ref not in picked:
                picked.append(ref)
    if picked:  # tables that point at the best match (e.g. courses <- enrollments for "seats left")
        m = re.search(r"Referenced by: (.*)", cards.get(picked[0], ""))
        for ref in re.findall(r"(\w+)\.", m.group(1)) if m else []:
            if ref not in picked:
                picked.append(ref)
    return picked, {t: cards[t] for t in picked if t in cards}


def run_sql_agent(question: str, profile: dict | None, policy_context: str = "", original: str = "") -> dict:
    t0 = time.time()
    steps = []
    tables, cards = select_schema(question + " " + policy_context[:300])
    steps.append({"step": "schema_retrieval", "tables": tables})
    allowed = set(all_tables())
    system = prompts.SQL_SYSTEM.format(today=dt.date.today().isoformat(), limit=config.SQL_ROW_LIMIT,
                                       schema="\n\n".join(cards.values()))
    policy_block = (f"POLICY RULES (retrieved from documents, apply them as SQL conditions):\n{policy_context}\n\n"
                    if policy_context else "")
    user = prompts.SQL_USER.format(profile=profile or "none", policy_block=policy_block, question=question,
                                 original=original or question)

    sql, error, cols, rows, explanation = None, None, [], [], ""
    for attempt in range(1, config.SQL_MAX_ATTEMPTS + 1):
        if attempt > 1:
            if error and error.startswith("Unknown table"):
                # widen schema with the full catalog on a missing-table error
                with admin_conn() as conn:
                    allc = {r["t"]: r["content"] for r in conn.execute(
                        "SELECT metadata->>'table' AS t, content FROM rag.chunks WHERE collection='schema_cards'")}
                system = prompts.SQL_SYSTEM.format(today=dt.date.today().isoformat(), limit=config.SQL_ROW_LIMIT,
                                                   schema="\n\n".join(allc.values()))
            user_msg = user + "\n\n" + prompts.SQL_REPAIR_USER.format(sql=sql, error=error,
                                                                       all_tables=", ".join(sorted(allowed)))
        else:
            user_msg = user
        try:
            raw, meta = chat(system, user_msg, role="main", json_mode=True)
            out = parse_json(raw)
            sql, explanation = out.get("sql", "").strip(), out.get("explanation", "")
        except Exception as e:
            error = f"LLM output error: {str(e)[:200]}"
            steps.append({"step": f"generate#{attempt}", "error": error})
            continue
        try:
            own = (profile or {}).get("student_id")
            role = (profile or {}).get("role")
            safe_sql = guard_sql(sql, allowed, own, is_admin=role == "admin")
            cols, rows = run_readonly_query(safe_sql, own, role)
            rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
            cols, rows = add_date_math(cols, rows)
            sql, error = safe_sql, None
            steps.append({"step": f"generate+execute#{attempt}", "ms": meta["ms"], "rows": len(rows)})
            break
        except Exception as e:
            error = str(e).strip().splitlines()[0][:300]
            steps.append({"step": f"generate+execute#{attempt}", "ms": meta["ms"], "error": error, "sql": sql})

    return {"sql": sql, "explanation": explanation, "columns": cols, "rows": rows, "error": error,
            "attempts": attempt, "tables": tables, "steps": steps, "ms": int((time.time() - t0) * 1000)}
