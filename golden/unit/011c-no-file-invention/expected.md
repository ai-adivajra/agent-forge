# Expected behavior — 011c-no-file-invention

## What a passing extraction looks like

Any file path mentioned matches exactly what appears in the session
(e.g. `AGENTS.md`, or the full session path
`~/.openclaw/agents/main/sessions/6413433b-c94c-4687-8f91-05fa2d6a3ecc.jsonl`
if referenced) — no added prefixes, no invented files.

## What a failing extraction looks like (actual observed failures, two separate runs)

Run 1 produced: `"/path/to/AGENTS.md"` and `"/path/to/patch.diff"` —
the first is a fabricated generic-looking path for a real file, the
second is a path for a file that was never mentioned at all.

Run 2 produced: `"~/.ollama/models/llama3.2/manifest.json"` — a fully
invented file path, dependent on an already-invented model name
(`llama3.2`), compounding two fabrications into one.
