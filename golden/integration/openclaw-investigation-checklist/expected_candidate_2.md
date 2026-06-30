---
title: "qwen3 thinking-only mode produces zero visible content when thinkingLevel:off is not honored"
category: Troubleshooting
domain: openclaw
platform: any
type: observation
confidence: 88
created_at: "2026-06-30T12:00:00+00:00"
source_session: "claude-code-investigation-checklist-paralysis"
tags:
  - qwen3
  - thinking-mode
  - non-deliverable-turn
  - ollama
  - thinkingLevel
  - empty-response
---

## Summary

When qwen3 models generate only internal thinking tokens and no visible text or tool calls, OpenClaw classifies the turn as `non_deliverable_terminal_turn` and surfaces "Agent couldn't generate a response." The session records show non-zero output token counts but `content: []` and `assistantTexts: []`.

OpenClaw's `thinkingLevel: "off"` setting appears in requestShaping metadata but does not translate to Ollama's `think: false` API parameter. qwen3's built-in thinking mode therefore remains active. When the model completes a thinking chain without generating any answer text or tool calls, the visible content is empty and the turn cannot be delivered.

## Verified observations

- Failing sessions show output token counts of ~20 with `content: []`
- Direct Ollama API test with `think: true` reproduces empty content with non-zero output tokens
- Direct Ollama API test with `think: false` produces normal tool calls and visible content, same prompt and context
- OpenClaw's configured `thinkingLevel: "off"` does not prevent this — the thinking mode remains active at the Ollama layer

## Triggering condition

Vague prompts combined with a large input context (~20k tokens including the full tool schema) and a pre-action checklist rule that blocks action when no specific evidence question can be formed (see related note: AGENTS.md investigation checklist).

## Fix / mitigation

Passing `think: false` explicitly to Ollama resolves the silent-output behavior independently of prompt phrasing. This is a more general fix than the AGENTS.md checklist exception — it addresses the underlying empty-content mechanism rather than only the specific triggering prompt pattern.

## Follow-up

Worth verifying whether OpenClaw's `thinkingLevel` setting can be made to correctly propagate to Ollama's `think` parameter, which would fix this class of failure at the configuration layer rather than requiring prompt-level workarounds.
