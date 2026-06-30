# Architecture — openclaw-knowledge

> This document describes the intended architecture of the project.
> It is the reference for all implementation decisions.
> Code follows the architecture — not the other way around.

---

## Purpose

Extract reusable technical knowledge from OpenClaw (Claude Code) sessions,
index it as vector embeddings, and inject the most relevant fragments back
into future OpenClaw sessions as contextual memory.

---

## Pipeline overview

```
OpenClaw
    │
    │  session.jsonl
    │  ~/.openclaw/agents/main/sessions/
    ▼
capture.py
    │
    │  Markdown notes with YAML frontmatter
    │
    ▼
KnowledgeBase/                     (Obsidian vault subfolder)
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
build_index.py                             validate.py
    │                                              │
    │  Reads all .md files                         │  Runs extraction on a
    │  Generates embeddings                        │  controlled fixture
    │  Stores vectors + metadata                   │  Compares to expected output
    │                                              │  Produces a quality report
    ▼                                              ▼
embeddings.sqlite                        golden/report.txt
    │
    ▼
search.py
    │
    │  Embeds the query
    │  Finds top-N nearest neighbours
    │
    ▼
inject.py
    │
    │  Formats results as OpenClaw memory context
    │
    ▼
OpenClaw
```

---

## Components

### One responsibility per program

| Program          | Input                          | Output                          | Never touches              |
|------------------|--------------------------------|---------------------------------|----------------------------|
| `capture.py`     | session `.jsonl`               | `.md` notes in KnowledgeBase    | index, search, golden      |
| `validate.py`    | golden fixture + expected note | quality report + diff           | KnowledgeBase, index       |
| `build_index.py` | `.md` notes in KnowledgeBase   | `embeddings.sqlite`             | sessions, inject, golden   |
| `search.py`      | natural language query         | ranked list of knowledge items  | sessions, inject, golden   |
| `inject.py`      | output of `search.py`          | OpenClaw context/memory file    | sessions, index, golden    |

These boundaries are strict. If a future change requires crossing them,
it is a signal to add a new program — not to expand an existing one.

**Key distinction:** `capture.py` runs against real sessions in production.
`validate.py` runs against controlled fixtures in isolation.
They share the same internal components (`parser`, `extractor`, `ollama`,
`knowledge`) but never call each other.

---

## Quality: Golden Dataset and validate.py

### Philosophy

The Golden Dataset is a quality assurance tool, not a project goal.
It grows naturally with the project: every new feature or prompt improvement
produces one new Golden case that validates it.

**Rule:** one new feature → one new Golden case.

Examples:
- Improve configuration extraction → add a Golden case for `Configuration`
- Add embedding search → add a Golden case for `Embeddings`
- Tighten workflow merging → add a Golden case for `Workflow`

This prevents the Golden Dataset from becoming a parallel project that
competes with development. It is always a by-product of real work.

### Structure

```
golden/
    README.md

    unit/                         recognition + composition + filtering + classification
        001-configuration/
        002-standard-workflow/
        003-adaptive-workflow/
        004-correction-loop/
        005-merge-shell-workflow/

    integration/                  real sessions — noise and volume
        rocm/                     pending
        telegram/                 pending

    regression/                   created on first production bug (not yet)
```

### validate.py

`validate.py` never calls `capture.py`. It calls the same internal
components directly, with a controlled fixture as input.

```
golden/cases/<N>/session.jsonl       (fixture — controlled input)
    │
    ├─ SessionParser.parse()
    ├─ Extractor.build_payload()
    ├─ Ollama.json()                 (calls the real model)
    ├─ KnowledgeCandidate.from_dict()
    │
    ▼
extracted note
    │
    ├─ compare to golden/cases/<N>/expected.md
    │
    ▼
golden/report.txt                    (pass / fail / diff per case)
```

### What validate.py checks

For each Golden case:

| Check                    | Method                                      |
|--------------------------|---------------------------------------------|
| Category matches         | exact string comparison                     |
| Confidence in range      | expected ± 15 (LLMs have natural variance)  |
| Key terms in summary     | presence check, not exact match             |
| Commands extracted       | set intersection ≥ 50%                      |
| No hallucinated fields   | no fields present in output but not fixture |

Exact text matching is never used for summaries — LLM output is
non-deterministic. Structural and semantic checks are sufficient.

### meta.yaml format

```yaml
id: "001"
title: "Configuration extraction"
feature: "prompt/configuration-rule"
sprint: 2
added: "2026-06-28"
min_confidence: 70
expected_category: "Configuration"
expected_terms:
  - "openclaw.json"
  - "agents.defaults"
```

---

## Existing files (Sprint 1–2, stable)

```
capture.py          entry point — session → notes
openclaw.py         read-only interface to ~/.openclaw/openclaw.json
parser.py           parse session .jsonl into typed events
extractor.py        convert events into LLM-ready payload
ollama.py           Ollama API client (chat, json, embed, ping)
prompts.py          SYSTEM_PROMPT for knowledge extraction
knowledge.py        KnowledgeCandidate dataclass + category validation
kb.py               save candidates as Obsidian markdown notes
config.py           load settings.yaml
settings.yaml       user configuration
```

---

## Planned: validate.py (Sprint 2.5)

```
validate.py         run Golden cases, compare to expected, produce report
golden/
    README.md
    cases/
        001-configuration/
            session.jsonl
            expected.md
            meta.yaml
```

Sprint 2.5 means: built between Sprint 2 (prompt quality) and Sprint 3
(index), so it is ready before the index introduces new failure modes.

---

## Planned: index/ (Sprint 3)

```
index/
    embed.py          thin wrapper around Ollama /api/embed
    vector_store.py   read/write embeddings.sqlite via sqlite-vec
    build_index.py    orchestrate: read notes → embed → store
    search.py         embed query → nearest neighbours → ranked results
```

### Why sqlite-vec

- No server, no daemon, no Docker
- Single `.sqlite` file — trivial to backup, copy, or delete
- Native Python bindings (`pip install sqlite-vec`)
- Available on Fedora via pip, no system packages required
- Sufficient for a personal knowledge base of thousands of notes
- Replaceable: `vector_store.py` is the only file that knows SQLite exists

### Why not ChromaDB / FAISS / Qdrant

They solve problems at a scale (millions of vectors, distributed queries,
persistent servers) that this project will not reach. Introducing them
now adds operational complexity with no benefit.

---

## Planned: inject.py (Sprint 4–5)

Two implementations, from simple to advanced:

**Version A — context file (no OpenClaw API required)**

`inject.py` writes a Markdown file to a known location that OpenClaw reads
at session start via its workspace or memory system:

```
~/.openclaw/memory/knowledge-context.md
```

Content format:
```markdown
# Relevant knowledge

## <title>  (confidence: 85/100)
<summary>
...
```

This version requires zero knowledge of OpenClaw internals.

**Version B — prompt injection (requires OpenClaw API)**

`inject.py` prepends the top-N items to the system prompt of the active
OpenClaw session via its API. Deferred until OpenClaw exposes a stable
injection endpoint.

Start with Version A. Upgrade to Version B only if Version A proves
insufficient in practice.

---

## Data flow: detailed

### capture.py

```
session.jsonl
    │
    ├─ SessionParser.parse()
    │       → list of typed Events
    │         (UserMessage, AssistantMessage, ToolCall, ToolResult)
    │
    ├─ Extractor.build_payload()
    │       → dict { model, turns: [...] }
    │
    ├─ Ollama.json()
    │       → { knowledge: [ { title, category, summary, ... } ] }
    │
    ├─ KnowledgeCandidate.from_dict()  ×N
    │       → validated, normalised candidates
    │
    └─ KnowledgeBase.save_all()
            → KnowledgeBase/<Category>/<slug>.md  ×N
```

### validate.py (planned)

```
golden/cases/<N>/session.jsonl       (controlled fixture)
    │
    ├─ SessionParser.parse()          same components as capture.py
    ├─ Extractor.build_payload()      same components as capture.py
    ├─ Ollama.json()                  same components as capture.py
    ├─ KnowledgeCandidate.from_dict() same components as capture.py
    │
    ├─ compare to golden/cases/<N>/expected.md
    │       category match, confidence range, term presence, command overlap
    │
    └─ write golden/report.txt
            PASS / FAIL / DIFF per case
```

### build_index.py (planned)

```
KnowledgeBase/**/*.md
    │
    ├─ read frontmatter + body
    │
    ├─ embed.embed_text(summary + title)
    │       → float[] via Ollama /api/embed
    │
    └─ vector_store.upsert(path, vector, metadata)
            → embeddings.sqlite
```

### search.py (planned)

```
query: str
    │
    ├─ embed.embed_text(query)
    │       → float[]
    │
    └─ vector_store.search(vector, top_n)
            → [ { path, title, summary, confidence, score } ]
```

### inject.py (planned)

```
results: list from search.py
    │
    ├─ format as Markdown context block
    │
    └─ write to ~/.openclaw/memory/knowledge-context.md
```

---

## Interface contracts

These are the stable interfaces between components.
Changing them requires updating this document first.

### KnowledgeBase note format

Every `.md` file in KnowledgeBase must have YAML frontmatter with at minimum:

```yaml
---
title: "..."
category: "..."       # one of the 9 allowed categories
confidence: 85        # integer 0–100
created_at: "..."     # ISO 8601 UTC
source_session: "..."
---
```

`build_index.py` and `validate.py` both depend on this contract.

### search.py output

`search.py` returns a list of dicts:

```python
[
    {
        "path":       "/path/to/note.md",
        "title":      "...",
        "category":   "...",
        "summary":    "...",
        "confidence": 85,
        "score":      0.92,    # cosine similarity, 0.0–1.0
    },
    ...
]
```

`inject.py` depends on this contract.

### Golden case format

Every Golden case is a directory under `golden/cases/` containing exactly:

```
session.jsonl     valid OpenClaw session format (may be hand-crafted)
expected.md       the note the extraction should produce
meta.yaml         case metadata (see above)
```

`validate.py` depends on this contract.

---

## Technical decisions (recorded)

| Decision                        | Choice             | Reason                                              | Revisit if                              |
|---------------------------------|--------------------|-----------------------------------------------------|-----------------------------------------|
| Vector store                    | sqlite-vec         | No daemon, single file, sufficient for personal KB  | Notes exceed ~500k or queries exceed 1s |
| Embedding model                 | Ollama (local)     | No API key, no cost, consistent with capture.py     | Quality proves insufficient             |
| Note format                     | Markdown + YAML    | Obsidian-compatible, human-readable, grep-able      | Switching away from Obsidian            |
| inject.py version               | A (context file)   | No OpenClaw internals required                      | OpenClaw exposes stable injection API   |
| Category vocabulary             | 9 fixed values     | Prevents drift, enables reliable filtering          | A category is consistently missing      |
| confidence scale                | 0–100 integer      | More natural for LLM output, readable in frontmatter| —                                       |
| Golden Dataset growth           | one case per feature| Prevents parallel-project drift                   | —                                       |
| validate.py vs capture.py       | separate programs  | validate.py uses same components, different inputs  | —                                       |
| Summary matching in validate.py | term presence only | LLM output is non-deterministic, exact match fails  | —                                       |

---

## Sprint plan

| Sprint | Goal                                        | Status      |
|--------|---------------------------------------------|-------------|
| 1      | capture.py — session → notes                | ✓ complete  |
| 2      | prompt quality + debug tooling              | ✓ complete  |
| 2.5    | validate.py + golden/ unit (001–005) + integration stubs | ✓ complete  |
| 3a     | index/ + build_index.py + inspect_index.py  | ✓ complete  |
| 3b     | search.py + search_demo.py                  | ✓ complete  |
| 4      | inject.py + inject_demo.py + tests/test_inject.py | ✓ complete  |
| 5      | OpenClaw integration                        | planned     |
| 6      | Session windowing (begin+end strategy)      | planned     |

---

## Replacement guide

This section documents how to replace each component without touching the others.

| Component to replace | Files to change                    | Contract to preserve                  |
|----------------------|------------------------------------|---------------------------------------|
| Ollama               | `ollama.py`, `index/embed.py`      | same `.chat()`, `.json()`, `.embed()` |
| Vector store         | `index/vector_store.py`            | same `upsert()` and `search()` API    |
| Obsidian             | `kb.py`                            | same YAML frontmatter contract        |
| OpenClaw             | `inject.py`, `openclaw.py`         | same session directory layout         |
| Embedding model      | `settings.yaml` (embedding key)    | none — model is configurable          |
| Golden case runner   | `validate.py`                      | same `golden/cases/` directory layout |
