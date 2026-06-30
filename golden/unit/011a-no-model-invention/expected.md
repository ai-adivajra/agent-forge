# Expected behavior — 011a-no-model-invention

## What a passing extraction looks like

Any model name appearing in any structured field or summary text is one
of the five models actually tested in the session: `qwen3:14b`,
`qwen3:30b-a3b`, `gemma3:12b`, `okamototk/gemma3-tools:12b`,
`qwen2.5-coder:14b` — or a generic reference ("the tested model").

## What a failing extraction looks like (actual observed failures, two separate runs)

Run 1 produced: "especially with smaller models like Llama3-8B"
Run 2 produced: "confirmed through testing with multiple models, including
Llama3-8B and Mistral-7B"

Neither `llama3-8b` nor `mistral-7b` (nor `llama3.2`, seen in a third run)
appear anywhere in the source session. This is a consistent, repeatable
fabrication pattern — not a one-off — across at least three separate
extraction attempts.
