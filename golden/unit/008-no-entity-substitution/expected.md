# Expected behavior — 008-no-entity-substitution

This case has no single "expected output" in the usual sense. Instead it
defines hard constraints that any valid output must satisfy, checked by
`expected_models` / `forbidden_terms` in meta.yaml rather than by prose
comparison.

## What a passing extraction looks like

Any candidate referencing a tested model uses one of:
- `qwen3:14b`
- `qwen3:30b-a3b`
- `gemma3:12b`
- `okamototk/gemma3-tools:12b`
- `qwen2.5-coder:14b`

or a generic phrase ("the tested model", "a smaller model", "the larger
variant") that does not name a specific model absent from the session.

## What a failing extraction looks like (actual observed failure)

The real capture.py run on this session produced:

> "...especially with smaller models like Llama3-8B..."
> "Commands: ollama run llama3-8b, ollama run llama3-70b"

Neither `llama3-8b` nor `llama3-70b` appear anywhere in `session.jsonl`.
This is a fabrication, not a paraphrase — it invented specific, plausible,
and verifiable-sounding facts that have no basis in the source material.
