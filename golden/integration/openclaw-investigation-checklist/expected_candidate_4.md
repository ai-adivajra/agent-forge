---
title: "qwen2.5-coder:14b hallucinates a non-existent invoke wrapper instead of calling registered tools"
category: Configuration
domain: openclaw
platform: any
type: capability
confidence: 85
created_at: "2026-06-30T12:00:00+00:00"
source_session: "claude-code-investigation-checklist-paralysis"
tags:
  - qwen2.5-coder
  - tool-hallucination
  - invoke
  - model-selection
  - format-mismatch
---

## Summary

When given OpenClaw's registered tool schema, qwen2.5-coder:14b generates calls to a non-existent `invoke` function with this format: `{"name": "invoke", "arguments": {"function_name": "...", "function_arguments": {...}}}`. No tool named `invoke` exists in OpenClaw's schema. The model never calls any of the actual registered tools, and OpenClaw cannot dispatch the output.

## Verified observations

- qwen2.5-coder:14b consistently generates the `invoke` wrapper pattern instead of using OpenClaw's actual tool names
- The failure is unrelated to prompt specificity — occurs on both vague and explicit prompts

## Capability statement

This is a tool-call format hallucination: the model applies a different calling convention than OpenClaw expects, likely learned from a different agentic framework's tool-calling format during training/fine-tuning.

## Recommendation

Verify tool-call format compatibility before using qwen2.5-coder:14b in any OpenClaw session that requires tool use. Like the gemma3 failures, this is unrelated to the AGENTS.md checklist issue — it is a separate, format-level incompatibility.
