#!/usr/bin/env python3

import json

from parser import (
    Event,
    UserMessage,
    AssistantMessage,
    ToolCall,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Deterministic structured-fact extraction (Deterministic First Principle)
# ---------------------------------------------------------------------------

_COMMAND_KEYS = frozenset({"command", "cmd", "shell", "bash", "script"})
_FILE_KEYS    = frozenset({"path", "file", "filename", "filepath"})
_MODEL_KEYS   = frozenset({"model", "model_id", "model_name"})


def extract_structured_facts(conversation: list[Event]) -> dict:
    """
    Extract commands, files, tool names, and model names directly from
    ToolCall events — no LLM involved, no hallucination possible.

    Returns:
        commands           — values of command-like arguments, deduplicated
        files              — values of path-like arguments, deduplicated
        tools              — tool names called, deduplicated
        models             — values of model-like arguments, deduplicated
        ask_llm_for_models — True iff no models were found structurally;
                             the LLM may then attempt text extraction as fallback
    """
    commands: list[str] = []
    files:    list[str] = []
    tools:    list[str] = []
    models:   list[str] = []

    seen_commands: set[str] = set()
    seen_files:    set[str] = set()
    seen_tools:    set[str] = set()
    seen_models:   set[str] = set()

    for event in conversation:
        if not isinstance(event, ToolCall):
            continue

        if event.name and event.name not in seen_tools:
            tools.append(event.name)
            seen_tools.add(event.name)

        if not isinstance(event.arguments, dict):
            continue

        for key, value in event.arguments.items():
            if not isinstance(value, str) or not value.strip():
                continue
            k = key.lower()

            if k in _COMMAND_KEYS and value not in seen_commands:
                commands.append(value)
                seen_commands.add(value)
            elif k in _FILE_KEYS and value not in seen_files:
                files.append(value)
                seen_files.add(value)
            elif k in _MODEL_KEYS and value not in seen_models:
                models.append(value)
                seen_models.add(value)

    return {
        "commands":             commands,
        "files":                files,
        "tools":                tools,
        "models":               models,
        "ask_llm_for_models":   len(models) == 0,
    }


class Extractor:
    """
    Converts a list of parsed session events into a payload dict
    suitable for sending to the LLM for knowledge extraction.
    """

    def build_payload(
        self,
        conversation: list[Event],
        model: str | None = None,
    ) -> dict:

        # Maximum payload size — model-dependent, not a project limit.
        # Configured under ollama.max_payload_chars in settings.yaml.
        # Future improvement: instead of cutting at the limit, use a
        # begin+end windowing strategy to preserve session conclusions.
        # (Planned for a future sprint — see ARCHITECTURE.md)
        from config import SETTINGS as _S
        max_chars = int(_S.get("ollama", {}).get("max_payload_chars", 60_000))

        payload: dict = {
            "model": model,
            "turns": [],
        }

        total_chars = 0

        for event in conversation:

            if isinstance(event, UserMessage):
                if not event.text.strip():
                    continue
                turn = {"role": "user", "content": event.text}

            elif isinstance(event, AssistantMessage):
                if not event.content.strip():
                    continue
                turn = {"role": "assistant", "content": event.content}

            elif isinstance(event, ToolCall):
                turn = {
                    "role":      "tool_call",
                    "tool":      event.name,
                    "arguments": event.arguments,
                }

            elif isinstance(event, ToolResult):
                content = event.content
                if len(content) > 2000:
                    content = content[:2000] + "\n…[truncated]"
                turn = {
                    "role":    "tool_result",
                    "tool":    event.tool,
                    "content": content,
                    "error":   event.error,
                }

            else:
                continue

            turn_chars = len(json.dumps(turn))
            if total_chars + turn_chars > max_chars:
                payload["turns"].append({
                    "role":    "system",
                    "content": f"[Session truncated — {len(conversation)} events total, "
                               f"payload limit {max_chars} chars reached]",
                })
                break

            payload["turns"].append(turn)
            total_chars += turn_chars

        return payload

    def build_prompt(
        self,
        conversation: list[Event],
        model: str | None = None,
    ) -> str:

        payload = self.build_payload(conversation=conversation, model=model)

        return json.dumps(payload, indent=2, ensure_ascii=False)
