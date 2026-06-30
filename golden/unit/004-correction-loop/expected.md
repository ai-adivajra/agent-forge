---
title: "OpenClaw error correction loop"
category: Workflow
confidence: 88
---

# OpenClaw error correction loop

## Summary

When `shell` returns an error, OpenClaw loops back to `write` to correct
the file, then re-executes `shell`. This repeats until `shell` returns a
clean result or the user confirms acceptance. The loop has no fixed
iteration limit.

## Tool Calls

- `write`
- `shell`

## Tags

#openclaw #workflow #error-handling #correction-loop
