---
title: "Standard OpenClaw agentic workflow"
category: Workflow
confidence: 90
---

# Standard OpenClaw agentic workflow

## Summary

A typical OpenClaw session follows the pattern: `web_search` to gather
external information → `read` to inspect existing files → `write` to
produce output → `shell` to verify the result. Steps can be skipped when
not needed — `web_search` is omitted for local-only tasks, `shell` is
omitted if verification is not required.

If `shell` returns an error or the output fails verification, OpenClaw
loops back to `write` to correct the file and calls `shell` again. This
correction loop repeats until the result is clean or the user confirms
acceptance.

## Tool Calls

- `web_search`
- `read`
- `write`
- `shell`

## Tags

#openclaw #workflow #agentic #tools #correction-loop
