# Architecture Status — v1.0

_Frozen snapshot of Agent Forge's foundational state, captured at the end
of the initial build-out phase. This document exists so that six months
from now, anyone (including future you) can answer: "what did v1 actually
look like, and why were these decisions made?"_

---

## Capture
**Status: Stable**

- `parser.py` — defensive parsing of OpenClaw session JSONL, tolerant of
  malformed events (see incident: missing `message` key crash, fixed)
- `extractor.py` — turn-building + `extract_structured_facts()` for
  deterministic commands/files/tools extraction (no LLM)
- `prompts.py` — citation framing for extractive fields, narrative
  faithfulness rules for summary/reason/notes
- `capture.py` — orchestrates parse → extract → LLM → merge deterministic
  facts → save knowledge notes
- Golden extraction dataset: `golden/unit/`, `golden/integration/`
- Campaign tooling: `run_campaign.sh`

**Known limitations:**
- `models` field has no reliable deterministic source — remains partially
  LLM-generated, measured at ~10-50% accuracy depending on session content
  (see `ARCHITECTURE.md` § Deterministic Extraction Principle, checklist)
- Narrative faithfulness (proposal vs. completed-fact) is not 100%
  reliable — case `golden/unit/010-investigation-faithfulness` documents
  a confirmed recurrence of this failure mode
- `category` classification is unstable across runs (same session can
  yield "Workflow", "Configuration", or "Troubleshooting") — currently
  treated as advisory in golden checks, not yet root-caused

## Index
**Status: Stable**

- `build_index.py` — incremental rebuild via content hashing, only
  re-embeds notes whose `embed_fields` actually changed
- `index/vector_store.py` — SQLite-backed, schema-versioned, brute-force
  cosine similarity (no ANN — fine at current scale, revisit if KB grows
  past ~thousands of notes)
- `index/embedder.py` — Ollama embedding API wrapper

## Retrieval
**Status: Stable**

- `search.py` — `Searcher` is the single production entry point; never
  bypassed by diagnostic or validation tooling (enforced by design)
- `RetrievalPolicy` (in `prime.py`) — focused/hybrid/exploratory routing
  based on score dominance, not domain keyword heuristics
- `explain_retrieval.py` — diagnostic-only tool, decomposes
  embedding + lexical score, never modifies live ranking
- `validate_retrieval.py` — golden retrieval dataset runner, campaign
  mode with `--runs N` for non-deterministic measurement

**Baseline v1.0** (golden/retrieval/, 10 runs):
```
Top-1 accuracy      : 100%  (20/20)
Top-3 recall        : 50%   (10/20)
False positive rate : 0%    (0/40)
'skill_workshop' avg rank : 1.00  (σ=0.00, stable)
'thinking-only'  avg rank : 4.00  (σ=0.00, stable — known failure)
```

**Known limitations:**
- Lexical scoring is an unweighted fraction (matched/total terms) — high
  frequency generic words ("agent", "not") carry the same weight as rare
  discriminating words ("skill_workshop"), confirmed to push a genuinely
  relevant note out of top-3 on conceptual queries
  (`golden/retrieval/002-vague-conceptual-query`)
- Not yet tested at scale beyond ~12 notes — domain routing and cutoff
  behavior may shift meaningfully as the knowledge base grows

## Injection
**Status: Stable**

- `inject.py` — writes `context/knowledge.md` to the OpenClaw workspace,
  structured as Engineering Context (observations / procedures / negative
  knowledge / open hypotheses)
- `prime.py` — orchestrates search → policy decision → render → inject,
  plus retrieval telemetry logging
- `tests/test_inject.py` — basic coverage

**Known limitations:**
- No equivalent golden dataset yet for injection/context quality
  (e.g. "does the injected context actually change session behavior?")
  — this is the next planned laboratory, deferred until retrieval and
  capture are exercised under real daily use

## Behavior Policy
**Status: Stable, lightly used**

- `BEHAVIOR.md` (workspace) — promoted rules + checklist, read by the
  agent at session start
- `investigate.py` — post-session failure analysis, LLM produces
  evidence-cited facts only; compliance and workflow decisions are
  deterministic Python, never LLM judgment calls
- One promoted rule candidate so far (process-tracking failures →
  test directly in terminal), not yet at 3-occurrence promotion threshold

---

## What v1.0 deliberately does NOT include

- Adapters for any agent runtime other than OpenClaw (the core pipeline
  is already runtime-agnostic in practice; formalizing an `adapters/`
  interface is deferred until a second real consumer exists)
- BM25, TF-IDF, cross-encoders, or any retrieval algorithm beyond cosine
  similarity + an experimental lexical-overlap rerank (diagnostic only,
  not in production)
- Automatic behavior-rule promotion (currently human-reviewed)
- A fourth "agent benchmark" laboratory measuring whether injected
  context actually improves a real subsequent session — this is the
  next major milestone, intentionally not started until v1's three
  existing laboratories (capture, retrieval, injection) have been
  exercised under real daily use rather than synthetic test sessions

## Why this document exists

Two measurable laboratories (extraction, retrieval) now exist, each
following the same discipline: golden cases written before code changes,
campaigns run before claiming improvement, known failures committed
openly rather than hidden. Every fix applied during the build-out phase
was discovered through actual investigation, not anticipated in advance
— the model-invention bug, the proposal-to-fact conversion bug, and the
conceptual-query lexical weakness were all found by using the system,
not by imagining edge cases.

The next phase is deliberately not "build more features." It is: use
Agent Forge for real, daily work, and let real friction generate the
next golden cases — the same way it has for every fix so far.
