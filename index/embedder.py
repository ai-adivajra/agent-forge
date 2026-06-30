"""
embedder.py — Convert text to embedding vectors via Ollama.

Single responsibility: text → list[float].
Knows nothing about notes, SQLite, or search.
"""

import sys
import os

# Allow running from project root or from index/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ollama import Ollama, OllamaError
from config import SETTINGS


class Embedder:

    def __init__(self, model: str | None = None):
        cfg         = SETTINGS.get("ollama", {})
        self.model  = model or cfg.get("embedding_model", "nomic-embed-text")
        self._ollama = Ollama()

    def embed(self, text: str) -> list[float]:
        """
        Return the embedding vector for the given text.
        Raises OllamaError if the model is unreachable or returns empty.
        """
        return self._ollama.embed(model=self.model, text=text)

    def embed_note(self, note: dict) -> list[float]:
        """
        Build the embedding text from a note dict using the fields
        declared in settings.yaml under index.embed_fields, then embed it.

        Default field order: title, category, summary, tags

        The text sent to the model looks like:
            Configuration
            OpenClaw configuration layout
            Configuration keys stored inside ~/.openclaw/openclaw.json
            Tags: configuration openclaw json
        """
        fields  = SETTINGS.get("index", {}).get(
            "embed_fields", ["title", "category", "summary", "tags"]
        )
        parts: list[str] = []

        for field in fields:
            value = note.get(field, "")
            if not value:
                continue
            if isinstance(value, list):
                # tags → space-joined
                value = " ".join(str(v) for v in value)
            parts.append(str(value).strip())

        text = "\n".join(parts)
        return self.embed(text)

    def ping(self) -> bool:
        return self._ollama.ping()

    @property
    def model_name(self) -> str:
        return self.model
