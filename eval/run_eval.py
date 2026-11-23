"""Evaluate routing intelligence (and optionally the full pipeline).

  python -m eval.run_eval            # router: tier-1 k-NN vs tier-2 LLM vs the production two-tier policy
  python -m eval.run_eval --full     # full pipeline: + SQL success, grounding, latency, tokens

Writes eval/results.md (used on the PPT results slide).
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.router import knn_classify, llm_route, route  # noqa: E402
from src.service import handle, load_profile  # noqa: E402

HERE = Path(__file__).resolve().parent
LABELS = ["SQL_ONLY", "VECTOR_ONLY", "HYBRID", "OUT_OF_SCOPE"]


def label_of(dec: dict) -> str:
    return "OUT_OF_SCOPE" if dec.get("in_scope") is False else dec["route"]


def confusion_md(conf: Counter) -> list[str]:
    lines = ["| expected \\ predicted | " + " | ".join(LABELS) + " |", "|---|" + "---|" * len(LABELS)]
    for e in LABELS:
        lines.append(f"| {e} | " + " | ".join(str(conf[(e, p)]) for p in LABELS) + " |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0, help="pause between questions (free-tier rate limits)")
    args = ap.parse_args()
    qs = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    n = len(qs)

    rows, conf = [], Counter()
    knn_ok = llm_ok = prod_ok = strat_ok = strat_n = fast_used = 0
    for i, item in enumerate(qs, 1):
        prof = load_profile(item["profile"])
        exp = item["route"]
        # tier 1 alone
        k = knn_classify(item["q"])
        knn_ok += k["label"] == exp
        # tier 2 alone
        try:
            out, _ = llm_route(item["q"], prof, [])
            llm_label = "OUT_OF_SCOPE" if out.get("in_scope") in (False, "false") else out["route"]
        except Exception:
            out, llm_label = {}, "ERROR"
        llm_ok += llm_label == exp
        # production two-tier policy
        t = time.time()
        dec = route(item["q"], prof, [])
        ms = int((time.time() - t) * 1000)
        pred = label_of(dec)
        prod_ok += pred == exp
        fast_used += dec["tier"] == "fast"
        if exp == "HYBRID" and pred == "HYBRID":
            strat_n += 1
            strat_ok += dec.get("strategy") == item.get("strategy")
        conf[(exp, pred)] += 1
        rec = {"q": item["q"], "expected": exp, "knn": k["label"], "knn_conf": k["confidence"], "llm": llm_label,
               "predicted": pred, "tier": dec["tier"], "strategy": dec.get("strategy"),
               "expected_strategy": item.get("strategy"), "route_ms": ms}
        if args.full:
            final = {}
            for ev in handle(item["q"], item["profile"], []):
                if ev["type"] in ("final", "blocked", "error"):
                    final = ev
            rec["latency_ms"] = final.get("latency_ms")
            rec["tokens"] = (final.get("usage") or {}).get("tokens")
            s = final.get("sql")
            rec["sql_ok"] = None if not s else s["error"] is None
            rec["sql_attempts"] = None if not s else s["attempts"]
            rec["grounding"] = (final.get("grounding") or {}).get("status")
        rows.append(rec)
        print(f"[{i:02d}] {'OK' if pred == exp else 'XX'} {exp:<12} knn={k['label'] or '-':<12} llm={llm_label:<12} "
              f"-> {pred:<12} ({dec['tier']}) {item['q'][:55]}")
        time.sleep(args.sleep)

    lines = ["# Evaluation results", "", f"Labelled questions: **{n}** (SQL_ONLY 10 · VECTOR_ONLY 8 · HYBRID 11 · OUT_OF_SCOPE 3)", "",
             "## Routing", "", "| Router | Accuracy | LLM calls for routing |", "|---|---|---|",
             f"| Tier 1 only: embedding k-NN (pgvector) | {knn_ok / n:.0%} | 0 |",
             f"| Tier 2 only: LLM router (gpt-oss-20b) | {llm_ok / n:.0%} | {n} |",
             f"| **Production: confidence-gated two-tier** | **{prod_ok / n:.0%}** | **{n - fast_used}** ({fast_used / n:.0%} routed without an LLM) |",
             "", f"HYBRID strategy (parallel vs sequential) correct: **{strat_ok}/{strat_n}**", ""]
    if args.full:
        def pct(xs):
            return f"{sum(xs) / len(xs):.0%}" if xs else "-"
        sq = [r for r in rows if r.get("sql_ok") is not None]
        g = [r for r in rows if r.get("grounding") in ("grounded", "revised")]
        lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms"))
        tok = [r["tokens"] for r in rows if r.get("tokens")]
        lines += ["## End-to-end", "", "| Metric | Value |", "|---|---|",
                  f"| SQL execution success | **{pct([r['sql_ok'] for r in sq])}** ({len(sq)} queries) |",
                  f"| SQL first-try success | {pct([r['sql_ok'] and r['sql_attempts'] == 1 for r in sq])} |",
                  f"| Answers grounded without revision | {pct([r['grounding'] == 'grounded' for r in g])} |",
                  f"| Median latency | {lat[len(lat) // 2] / 1000:.1f}s |" if lat else "| Median latency | - |",
                  f"| Avg tokens per answer | {sum(tok) // max(len(tok), 1):,} |", ""]
    lines += ["## Confusion matrix (production router)", ""] + confusion_md(conf)
    lines += ["", "## Misses", ""] + [f"- `{r['expected']}` → `{r['predicted']}` ({r['tier']}): {r['q']}" for r in rows
                                      if r["predicted"] != r["expected"]]
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / f"results_{'full' if args.full else 'router'}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
