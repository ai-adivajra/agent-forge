---
title: "gemma3:12b rejects OpenClaw's full tool payload — not usable for tool-requiring tasks"
category: Configuration
domain: openclaw
platform: any
type: capability
confidence: 85
created_at: "2026-06-30T12:00:00+00:00"
source_session: "claude-code-investigation-checklist-paralysis"
tags:
  - gemma3
  - tool-schema
  - schema-rejection
  - model-selection
  - compatibility
---

## Summary

gemma3:12b returns a provider-level error ("rejected the request schema or tool payload") when OpenClaw sends its full tool schema. The failure occurs before any reasoning or tool selection — the model does not produce any response. This is a schema compatibility failure, not a prompt clarity or model capability failure; the same error occurs regardless of prompt content.

okamototk/gemma3-tools:12b (the tool-calling variant) does not resolve the issue. It fails with a different error (`incomplete_turn`, "Agent couldn't generate a response") and also produces no output.

## Verified observations

- gemma3:12b: tool schema rejected at provider layer, fails on both vague and explicit prompts
- okamototk/gemma3-tools:12b: different failure mode (`incomplete_turn`), also fails on the same test prompt

## Capability statement

Neither gemma3 variant is a viable drop-in replacement for qwen3 in an OpenClaw session that requires tool use, as of this testing date. This is unrelated to the AGENTS.md checklist issue — swapping to gemma3 does not work around that problem, it introduces a separate, unrelated failure.

## Recommendation

Verify tool-schema compatibility before selecting a model for OpenClaw use. A model working well in other agentic harnesses (e.g. via a different tool-calling convention) does not guarantee compatibility with OpenClaw's specific tool payload format.
