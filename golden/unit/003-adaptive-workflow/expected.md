---
title: "OpenClaw workflow adapts to task type"
category: Workflow
confidence: 85
---

# OpenClaw workflow adapts to task type

## Summary

OpenClaw skips workflow steps that are not needed for the current task.
`web_search` is omitted for local-only tasks. `shell` is omitted when
verification is not required. The overall direction (gather → analyse →
produce → verify) is consistent, but individual steps are conditional.

## Tool Calls

- `web_search`
- `shell`

## Tags

#openclaw #workflow #adaptive
