# Evaluation results

Labelled questions: **32** (SQL_ONLY 10 · VECTOR_ONLY 8 · HYBRID 11 · OUT_OF_SCOPE 3)

## Routing

| Router | Accuracy | LLM calls for routing |
|---|---|---|
| Tier 1 only: embedding k-NN (pgvector) | 62% | 0 |
| Tier 2 only: LLM router (gpt-oss-20b) | 91% | 32 |
| **Production: confidence-gated two-tier** | **94%** | **29** (9% routed without an LLM) |

HYBRID strategy (parallel vs sequential) correct: **8/9**

## Confusion matrix (production router)

| expected \ predicted | SQL_ONLY | VECTOR_ONLY | HYBRID | OUT_OF_SCOPE |
|---|---|---|---|---|
| SQL_ONLY | 10 | 0 | 0 | 0 |
| VECTOR_ONLY | 0 | 8 | 0 | 0 |
| HYBRID | 0 | 2 | 9 | 0 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 3 |

## Misses

- `HYBRID` → `VECTOR_ONLY` (llm): How much is the Women in STEM scholarship and what are the renewal conditions?
- `HYBRID` → `VECTOR_ONLY` (llm): What is the deadline for the Sports Excellence Award and how do I apply?
