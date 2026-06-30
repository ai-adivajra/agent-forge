"""
inject.py — Write retrieval results as a context file.

Single responsibility: list[SearchResult] → files on disk.

Writes two files to the configured context directory:
    knowledge.md      Markdown context ready to be read by any agent
    retrieval.json    Retrieval metadata for debugging

Knows nothing about SQLite, Ollama, or OpenClaw.
Called by inject_demo.py (interactive) and future Sprint 5 integration.

Contract:
    injector = Injector()
    paths    = injector.inject(question, results)
    # → InjectionResult(knowledge_path, retrieval_path, notes_written)
"""

import json
import sys
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SETTINGS, expand
from index.vector_store import SearchResult


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class InjectionResult:
    knowledge_path: Path
    retrieval_path: Path
    notes_written:  int
    query:          str


# ---------------------------------------------------------------------------
# Injector
# ---------------------------------------------------------------------------

class Injector:

    def __init__(self, context_dir: str | Path | None = None):
        cfg = SETTINGS.get("inject", {})

        resolved = expand(
            context_dir or cfg.get("context_dir", "~/.openclaw/workspace/context")
        )
        self.context_dir      = resolved
        self.knowledge_file   = cfg.get("knowledge_file", "knowledge.md")
        self.retrieval_file   = cfg.get("retrieval_file", "retrieval.json")

        self.context_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject(
        self,
        question: str,
        results:  list[SearchResult],
    ) -> InjectionResult:
        """
        Render results to knowledge.md and retrieval.json.
        Returns an InjectionResult with the paths and note count.
        """
        knowledge_path = self.context_dir / self.knowledge_file
        retrieval_path = self.context_dir / self.retrieval_file

        knowledge_path.write_text(
            self._render_knowledge(question, results),
            encoding="utf-8",
        )
        retrieval_path.write_text(
            self._render_retrieval(question, results),
            encoding="utf-8",
        )

        return InjectionResult(
            knowledge_path = knowledge_path,
            retrieval_path = retrieval_path,
            notes_written  = len(results),
            query          = question,
        )

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_knowledge(
        self,
        question: str,
        results:  list[SearchResult],
    ) -> str:
        """Render results as a Markdown context block."""

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = [
            "# Relevant Knowledge",
            "",
            f"> Query: {question}  ",
            f"> Generated: {now}  ",
            f"> Sources: {len(results)}",
            "",
        ]

        if not results:
            lines += [
                "_No relevant knowledge found for this query._",
                "",
            ]
            return "\n".join(lines)

        for i, r in enumerate(results, 1):
            lines += [
                f"## {i}. {r.title}",
                "",
                f"> **Category:** {r.category}  ",
                f"> **Score:** {r.score:.4f}  ",
                f"> **Confidence:** {r.confidence}/100",
                "",
                "### Summary",
                "",
                r.summary,
                "",
            ]
            if r.tags:
                lines += [
                    "### Tags",
                    "",
                    " ".join(f"`{t}`" for t in r.tags),
                    "",
                ]
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _render_retrieval(
        self,
        question: str,
        results:  list[SearchResult],
    ) -> str:
        """Render retrieval metadata as JSON."""
        payload = {
            "query":        question,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "top_k":        len(results),
            "results": [
                {
                    "rank":       i,
                    "path":       r.path,
                    "title":      r.title,
                    "category":   r.category,
                    "score":      r.score,
                    "confidence": r.confidence,
                    "tags":       r.tags,
                }
                for i, r in enumerate(results, 1)
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
