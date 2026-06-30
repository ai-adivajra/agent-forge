import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class Event:

    def __init__(self, raw: dict):
        self.raw  = raw
        self.type = raw.get("type")


class UserMessage(Event):

    def __init__(self, raw: dict):
        super().__init__(raw)
        message = raw.get("message") or {}
        content = message.get("content", "")
        # content can be a plain string or a list of blocks
        if isinstance(content, list):
            self.text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            self.text = content or ""


class AssistantMessage(Event):

    def __init__(self, raw: dict):
        super().__init__(raw)
        message = raw.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            self.content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            self.content = content or ""


class ToolCall(Event):

    def __init__(self, raw: dict):
        super().__init__(raw)
        message = raw.get("message") or {}
        content = message.get("content")
        tool = content[0] if isinstance(content, list) and content else {}
        if not isinstance(tool, dict):
            tool = {}
        self.name      = tool.get("name", "")
        self.arguments = tool.get("arguments", tool.get("input", {}))


class ToolResult(Event):

    def __init__(self, raw: dict):
        super().__init__(raw)
        msg = raw.get("message") or {}
        self.tool    = msg.get("toolName", "")
        self.error   = msg.get("isError", False)
        self.details = msg.get("details", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            self.content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict)
            )
        else:
            self.content = content or ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class SessionParser:

    def __init__(self, session: str | Path):
        self.session = Path(session)

    def parse(self) -> list[Event]:
        """
        Parse the JSONL session file and return a flat list of typed events.
        """
        objects: list[Event] = []

        with open(self.session, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    j = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if j.get("type") != "message":
                    continue

                message = j.get("message")
                if not isinstance(message, dict):
                    continue   # malformed event — skip rather than crash

                role = message.get("role", "")

                if role == "user":
                    objects.append(UserMessage(j))

                elif role == "assistant":
                    c = message.get("content", "")
                    if isinstance(c, list) and c and isinstance(c[0], dict):
                        if c[0].get("type") == "toolCall":
                            objects.append(ToolCall(j))
                        else:
                            objects.append(AssistantMessage(j))
                    else:
                        objects.append(AssistantMessage(j))

                elif role == "toolResult":
                    objects.append(ToolResult(j))

        return objects

    def conversation(self) -> list[Event]:
        """
        Alias for parse() — returns the full conversation event list.
        capture.py calls this method.
        """
        return self.parse()
