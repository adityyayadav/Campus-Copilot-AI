"""Schema mapping: turns the relational database into retrievable knowledge.

Layer 1, schema cards: one card per table, auto-generated from the Postgres catalog
          (column types, COMMENTs, PK/FK links, sample rows, allowed categorical values).
          The SQL agent retrieves only the relevant cards instead of dumping the whole schema.
Layer 2, table-to-text: descriptive rows (grants, scholarships, faculty, courses) are verbalised
          into sentences and indexed next to the documents, tagged with table + primary key, so
          semantic search can find structured entities and SQL can join back for live values.
Layer 3, value index: distinct values of low-cardinality text columns are listed in the cards
          so the LLM maps "CS" -> 'CSE', "hostel fee" -> 'HOSTEL'.
"""
from . import config
from .db import admin_conn

MAX_DISTINCT = 12
PERSONAL_TABLES = {"students", "fee_ledger", "grant_applications", "enrollments"}


def _catalog(conn) -> dict:
    tables = [r["table_name"] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
        "AND table_type='BASE TABLE' ORDER BY table_name")]
    info = {}
    for t in tables:
        tdesc = conn.execute("SELECT obj_description(%s::regclass, 'pg_class') AS d", (f"public.{t}",)).fetchone()["d"]
        # only columns the read-only SQL role may actually SELECT are exposed to the LLM
        cols = conn.execute("""
            SELECT c.column_name, c.data_type, c.ordinal_position,
                   col_description(%s::regclass, c.ordinal_position) AS descr
            FROM information_schema.columns c
            WHERE c.table_schema='public' AND c.table_name=%s
              AND has_column_privilege(%s, %s::regclass, c.column_name, 'SELECT')
            ORDER BY c.ordinal_position""",
                            (f"public.{t}", t, config.READER_USER, f"public.{t}")).fetchall()
        pks = {r["column_name"] for r in conn.execute("""
            SELECT kcu.column_name FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
            WHERE tc.table_name=%s AND tc.constraint_type='PRIMARY KEY'""", (t,))}
        fks = {r["column_name"]: f'{r["ref_table"]}.{r["ref_col"]}' for r in conn.execute("""
            SELECT kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_col
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name=ccu.constraint_name
            WHERE tc.table_name=%s AND tc.constraint_type='FOREIGN KEY'""", (t,))}
        rowcount = conn.execute(f'SELECT count(*) AS n FROM public."{t}"').fetchone()["n"]
        for c in cols:
            c["values"] = None
            if c["data_type"] == "text" and c["column_name"] not in pks:
                vals = conn.execute(
                    f'SELECT DISTINCT "{c["column_name"]}" AS v FROM public."{t}" WHERE "{c["column_name"]}" IS NOT NULL '
                    f'ORDER BY 1 LIMIT {MAX_DISTINCT + 1}').fetchall()
                if len(vals) <= MAX_DISTINCT:
                    c["values"] = [v["v"] for v in vals]
        # personal tables: never put real people's rows into the prompt
        samples = [] if t in PERSONAL_TABLES else conn.execute(
            "SELECT " + ", ".join(f'"{c["column_name"]}"' for c in cols) + f' FROM public."{t}" LIMIT 3').fetchall()
        info[t] = {"description": tdesc or "", "columns": cols, "pks": pks, "fks": fks,
                   "rowcount": rowcount, "samples": samples}
    for t, i in info.items():
        i["referenced_by"] = sorted({f"{o}.{col}" for o, oi in info.items() for col, ref in oi["fks"].items()
                                     if ref.startswith(f"{t}.")})
    return info


def _card_text(t: str, i: dict) -> str:
    lines = [f"TABLE {t}  ({i['rowcount']} rows)", f"Description: {i['description']}", "Columns:"]
    for c in i["columns"]:
        tags = []
        if c["column_name"] in i["pks"]:
            tags.append("PK")
        if c["column_name"] in i["fks"]:
            tags.append(f"FK -> {i['fks'][c['column_name']]}")
        line = f"  - {c['column_name']} ({c['data_type']}{', ' + ', '.join(tags) if tags else ''})"
        if c["descr"]:
            line += f": {c['descr']}"
        if c["values"]:
            line += f"  [values: {', '.join(map(str, c['values']))}]"
        lines.append(line)
    if i.get("referenced_by"):
        lines.append("Referenced by: " + ", ".join(i["referenced_by"]))
    if i["samples"]:
        lines.append("Sample rows:")
        for s in i["samples"]:
            lines.append("  " + ", ".join(f"{k}={v}" for k, v in s.items()))
    return "\n".join(lines)


def build_schema_cards() -> list[dict]:
    with admin_conn() as conn:
        cat = _catalog(conn)
    out = []
    for t, i in cat.items():
        text = _card_text(t, i)
        out.append({
            "collection": "schema_cards", "source": f"db:{t}", "title": f"Table {t}", "section": "schema",
            "content": text, "embed_text": text,
            "metadata": {"table": t, "columns": [c["column_name"] for c in i["columns"]]},
        })
    return out


def build_table_rows() -> list[dict]:
    out = []
    with admin_conn() as conn:
        for r in conn.execute("""
            SELECT g.*, COALESCE(d.name, 'all departments (interdisciplinary)') AS dept_name
            FROM research_grants g LEFT JOIN departments d ON d.dept_id = g.dept_id"""):
            text = (f"Research grant {r['grant_id']} '{r['title']}' sponsored by {r['sponsor']}. Tier {r['tier']} grant "
                    f"open to {r['dept_name']}. Focus area: {r['focus_area']}. Amount INR {r['amount']:,.0f} for "
                    f"{r['duration_months']} months. Application deadline {r['deadline']:%d %B %Y}. Status {r['status']}.")
            out.append(_row("research_grants", r["grant_id"], text,
                            {"dept_id": r["dept_id"], "tier": r["tier"], "status": r["status"]}))
        for r in conn.execute("SELECT * FROM scholarships"):
            text = (f"Scholarship {r['scholarship_id']} '{r['name']}', category {r['category']}. Annual amount INR "
                    f"{r['amount']:,.0f}, {r['seats']} seats. Last date to apply {r['deadline']:%d %B %Y}. Status {r['status']}.")
            out.append(_row("scholarships", r["scholarship_id"], text, {"category": r["category"], "status": r["status"]}))
        for r in conn.execute("""SELECT f.*, d.name AS dept_name FROM faculty f JOIN departments d USING (dept_id)"""):
            text = (f"{r['name']} ({r['faculty_id']}) is a {r['designation']} in {r['dept_name']} working on "
                    f"{r['research_area']}. Can mentor research grants in this area. Email {r['email']}.")
            out.append(_row("faculty", r["faculty_id"], text, {"dept_id": r["dept_id"]}))
        for r in conn.execute("""SELECT c.*, d.name AS dept_name FROM courses c JOIN departments d USING (dept_id)"""):
            text = (f"Course {r['course_id']} '{r['title']}' is a {r['course_type'].lower()} course of {r['dept_name']} for "
                    f"year {r['year']} students, {r['credits']} credits, offered in term {r['term']}.")
            out.append(_row("courses", r["course_id"], text, {"dept_id": r["dept_id"], "term": r["term"]}))
    return out


_TOPIC = {"research_grants": "research_grants", "scholarships": "scholarships",
          "faculty": "research_grants", "courses": "academics"}


def _row(table: str, pk: str, text: str, meta: dict) -> dict:
    return {"collection": "table_rows", "source": f"db:{table}", "title": f"{table} row {pk}", "section": table,
            "content": text, "embed_text": text,
            "metadata": {"table": table, "pk": pk, "topic": _TOPIC.get(table, table), **meta}}


def schema_card_texts(tables: list[str] | None = None) -> dict[str, str]:
    """Direct (non-vector) access to cards, used as a fallback when retrieval misses a table."""
    with admin_conn() as conn:
        rows = conn.execute("SELECT metadata->>'table' AS t, content FROM rag.chunks WHERE collection='schema_cards'").fetchall()
    return {r["t"]: r["content"] for r in rows if tables is None or r["t"] in tables}
