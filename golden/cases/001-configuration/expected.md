---
title: "OpenClaw configuration structure and model keys"
category: Configuration
confidence: 88
---

# OpenClaw configuration structure and model keys

## Summary

OpenClaw stores its main configuration at `~/.openclaw/openclaw.json`.
The active chat model is set via `agents.defaults.model.primary` and
fallbacks are listed under `agents.defaults.model.fallbacks`.
The embedding model used for memory search is configured separately at
`agents.defaults.memorySearch.model` and must be an embedding-capable
model such as `nomic-embed-text` or `mxbai-embed-large`.
The default workspace for all agents is set at `agents.defaults.workspace`
and can be overridden per agent.

## Files

- `~/.openclaw/openclaw.json`

## Tags

#openclaw #configuration #model #embeddings #workspace
