# Routing: design and evaluation of classifier options

The router decides, for every question, whether to hit **SQL_ONLY**, **VECTOR_ONLY** or **HYBRID** (parallel or sequential), plus an out-of-scope gate. Our measured results are in `eval/results.md`.

## Options considered

| Option | How it runs | Latency | Cost | Fit for this project |
|---|---|---|---|---|
| **Laya** (Convai Innovations, Apache-2.0, 421M ModernBERT-large; v0.5 adds a 0.8B–27B frozen LLM reader) | **Local only**: `pip install laya` pulls torch + transformers, ~800 MB checkpoint download | Vendor figures: 33–40 ms on GPU, 113–464 ms on CPU | Free, but uses local CPU/RAM | ❌ Not adopted as default (see below) |
| Jev (TypeSafe AI) | Proprietary hosted decision API | low | Paid per token | ❌ Not free |
| **Embedding k-NN (ours, tier 1)** | Reuses the question embedding already computed for the cache and retrieval; cosine k-NN over 52 labelled exemplars in pgvector | ~5–40 ms | **0 extra API calls** | ✅ Adopted |
| **LLM router (ours, tier 2)** | `gpt-oss-20b` on Groq, JSON output with self-reported confidence | ~0.5–2 s | 1 small-model call | ✅ Adopted for uncertain / HYBRID / follow-up questions |

### Why Laya was evaluated but not made the default
1. **Requirement conflict.** The brief asks for no unnecessary local CPU/GPU load. Laya is local-only: no hosted API was found, and it needs torch plus a ~0.8 GB model.
2. **Zero-shot accuracy is not established for our labels.** The vendor's own benchmark shows the base English checkpoint at **0.362** zero-shot on typed decisions (below a 0.461 majority baseline). Accuracy "jumps" only after fitting on labelled domain data, so we would need a labelled routing set either way.
3. **Calibration.** The README states shipped checkpoints are over-confident until temperatures are refit, which undermines confidence-based routing out of the box.
4. **Our k-NN tier covers the same role at zero marginal cost**, using the embedding the pipeline already computes. It has the same "fast reflex classifier before the LLM" architecture Laya is designed for.

**Upgrade path:** if self-hosting is acceptable, Laya can replace tier 1 behind the same interface (`knn_classify` → `{label, confidence}`). Fit it on `src/route_exemplars.py` plus the logged production routes in `ops.request_log`, and gate on its `route == "auto"` output.

## Production policy (confidence-based with fallbacks)
```
question ─► tier 1 k-NN  ── confident (≥ 0.75, calibrated: 5/5 correct above it on the eval set) and SQL_ONLY / VECTOR_ONLY / out-of-scope and no chat history ─► accept (no LLM)
               │
               └─ otherwise ─► tier 2 LLM router ── confidence ≥ 0.55 ─► accept (incl. HYBRID strategy + sub-questions)
                                   │
                                   ├─ confidence < 0.55 ─► HYBRID/parallel (query both: costs more, never misses a source)
                                   └─ error / timeout   ─► tier-1 label if ≥ 0.5, else HYBRID/parallel
```
