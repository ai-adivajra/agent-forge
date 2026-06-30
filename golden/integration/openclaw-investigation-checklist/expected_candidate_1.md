---
title: "AGENTS.md investigation checklist creates unsatisfiable precondition for broad prompts"
category: Troubleshooting
domain: openclaw
platform: any
type: incident
confidence: 90
created_at: "2026-06-30T12:00:00+00:00"
source_session: "claude-code-investigation-checklist-paralysis"
tags:
  - investigation-discipline
  - checklist
  - vague-prompt
  - intermittent-failure
  - agents-md
---

## Summary

The investigation discipline section of AGENTS.md requires the agent to form a specific evidence question before each tool call and prohibits acting if it cannot: "If you cannot answer the first question, do not make the tool call." For broad or non-specific prompts (e.g. "Check system health"), no single specific evidence question can be formed across all possible tool calls. This creates an unsatisfiable precondition that blocks the agent from acting.

The failure is probabilistic, not deterministic. Smaller models fail consistently. Larger or more capable models fail intermittently — succeeding on some attempts and silently producing no output on others, within the same session context. Do not conclude that a more capable model is immune; it is less susceptible, not immune.

## Verified observations

- qwen3:14b fails consistently on "Check system health" with unpatched AGENTS.md (16-20 output tokens, empty content)
- qwen3:30b-a3b fails twice, then succeeds once, all three attempts with unpatched AGENTS.md — confirming the failure is intermittent even on a larger model, not deterministic
- qwen3:30b-a3b succeeds reliably once AGENTS.md is patched with the exception clause below
- The same investigation discipline rule does not impede specific/explicit prompts (e.g. "Run df -h, free -h, uptime and summarize")

## Disproven hypothesis

**Do not conclude "model size alone determines whether this checklist rule causes failure."**
A single success on a larger model does not prove immunity — only repeated clean trials across multiple attempts establish whether a model is reliably unaffected.

## Fix

Add an explicit exception permitting broad diagnostic surveys as a valid default first action when no single evidence question can be formed for a general status or health request:

```markdown
If you cannot answer the first question, do not make the tool call.

**Exception:** For general status or health-check requests, a broad
diagnostic survey (e.g. `uptime`, `df -h`, `free -h`, `ps`) counts as a
valid starting action — default to that rather than refusing to act.
```

## Investigation procedure

1. If a broad/vague prompt produces silent agent failure, check whether a system-prompt rule imposes a precondition before tool calls (e.g. "state a specific question first").
2. Test with an explicit, narrow version of the same prompt — if it succeeds where the vague version fails, the precondition is the likely cause.
3. Before concluding a fix works, run multiple clean attempts (not just one) — intermittent failures can produce a false "it works now" on a single lucky run.
4. When changing an instruction mid-test, discard that specific attempt's result — do not draw conclusions from a run where two variables changed simultaneously.
