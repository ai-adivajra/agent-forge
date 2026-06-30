# Expected behavior — 009-independent-findings

## What a passing extraction looks like

At minimum 3 separate KnowledgeCandidates, each addressing one of:

1. **Checklist paralysis** — the AGENTS.md precondition rule blocks vague
   prompts, affecting qwen3:14b consistently and qwen3:30b-a3b intermittently
2. **Schema rejection** — gemma3:12b and gemma3-tools:12b fail at the
   provider layer regardless of prompt content (unrelated to finding 1)
3. **Tool-call hallucination** — qwen2.5-coder:14b invents a non-existent
   `invoke()` function instead of using registered tools (unrelated to
   finding 1 and 2)

A 4th candidate covering the intermittent nature of qwen3:30b-a3b's
failure (succeeds sometimes, fails sometimes, under identical conditions)
is a bonus but not required for a pass — it can reasonably be folded into
candidate 1 as a nuance, since it's about the same root cause.

## What a failing extraction looks like (actual observed failure)

The real capture.py run on this session produced exactly 2 candidates,
both about the AGENTS.md checklist issue (the root cause and its fix).
Findings 2 and 3 — gemma3's schema rejection and qwen2.5-coder's
hallucination — were entirely absent from the output. The investigation
spent roughly a third of its tool calls establishing these two findings;
none of that effort survived extraction.
