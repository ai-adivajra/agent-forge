#!/usr/bin/env python3

import json

from parser import (
    Event,
    UserMessage,
    AssistantMessage,
    ToolCall,
    ToolResult,
)


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
