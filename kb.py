"""
KnowledgeBase — persists KnowledgeCandidate objects as Obsidian markdown notes.

Directory layout inside the vault:
  KnowledgeBase/
    <Category>/
      <slug>.md
"""

import re
import logging
from pathlib import Path
from datetime import datetime, timezone

from knowledge import KnowledgeCandidate, FALLBACK_CATEGORY
from config import SETTINGS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert a title to a safe filename slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    lines = "\n".join(f"  - \"{item}\"" for item in items)
    return f"\n{lines}"


def _render_note(c: KnowledgeCandidate) -> str:
    """Render a KnowledgeCandidate as an Obsidian markdown note."""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- YAML frontmatter ------------------------------------------------
    fm: list[str] = [
        "---",
        f"title: \"{c.title}\"",
        f"category: {c.category}",
        f"confidence: {c.confidence}",
        f"created_at: {c.created_at}",
        f"source_session: \"{c.source_session}\"",
    ]

    if c.domain:
        fm.append(f"domain: \"{c.domain}\"")
    if c.platform:
        fm.append(f"platform: \"{c.platform}\"")
    if c.type:
        fm.append(f"type: {c.type}")

    if c.reason:
        fm.append(f"reason: \"{c.reason}\"")

    if c.tags:
        tag_list = ", ".join(f'"{t}"' for t in c.tags)
        fm.append(f"tags: [{tag_list}]")

    if c.models:
        fm.append(f"models:{_yaml_list(c.models)}")

    fm.append("---")

    # ---- Body ------------------------------------------------------------
    lines: list[str] = fm + [
        "",
        f"# {c.title}",
        "",
        f"> **Category:** {c.category}  ",
        f"> **Confidence:** {c.confidence}/100  ",
        f"> **Captured:** {now}",
        "",
    ]

    if c.reason:
        lines += [
            "> [!info] Why this was retained",
            f"> {c.reason}",
            "",
        ]

    lines += [
        "## Summary",
        "",
        c.summary,
        "",
    ]

    if c.tool_calls:
        lines += ["## Tool Calls", ""]
        for t in c.tool_calls:
            lines.append(f"- `{t}`")
        lines.append("")

    if c.commands:
        lines += ["## Commands", ""]
        for cmd in c.commands:
            lines += ["```bash", cmd, "```", ""]

    if c.files:
        lines += ["## Files", ""]
        for f in c.files:
            lines.append(f"- `{f}`")
        lines.append("")

    if c.models:
        lines += ["## Models", ""]
        for m in c.models:
            lines.append(f"- {m}")
        lines.append("")

    if c.plugins:
        lines += ["## Plugins", ""]
        for p in c.plugins:
            lines.append(f"- {p}")
        lines.append("")

    if c.notes:
        lines += ["## Notes", "", c.notes, ""]

    if c.tags:
        lines += [
            "## Tags",
            "",
            " ".join(f"#{t}" for t in c.tags),
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """
    Persists knowledge candidates to an Obsidian vault.

    Usage:
        kb = KnowledgeBase(knowledge_dir)
        saved = kb.save_all(candidates, min_confidence=75)
    """

    def __init__(self, knowledge_dir: str | Path):
        self.root = Path(knowledge_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self, candidate: KnowledgeCandidate) -> Path:
        """
        Save one candidate as a markdown note.
        Returns the path of the written file.
        """
        # Use FALLBACK_CATEGORY folder if category was not recognised
        cat_folder = candidate.category if candidate.category != FALLBACK_CATEGORY else "_unknown"
        category_dir = self.root / cat_folder
        category_dir.mkdir(parents=True, exist_ok=True)

        filename = _slug(candidate.title) + ".md"
        path = category_dir / filename

        # Avoid silently overwriting — append a timestamp on collision
        if path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            filename = _slug(candidate.title) + f"-{ts}.md"
            path = category_dir / filename

        note = _render_note(candidate)
        path.write_text(note, encoding="utf-8")

        log.info("Saved: %s", path)
        return path

    def save_all(
        self,
        candidates: list[KnowledgeCandidate],
        min_confidence: int | None = None,
    ) -> list[Path]:
        """
        Save all candidates that meet the confidence threshold (0–100).
        Returns list of saved paths.
        """
        if min_confidence is None:
            raw = SETTINGS.get("knowledge", {}).get("confidence", 0.75)
            # Accept both 0.75 (old float) and 75 (new int) in settings.yaml
            min_confidence = int(raw * 100) if raw <= 1.0 else int(raw)

        saved: list[Path] = []

        for c in candidates:
            if c.confidence < min_confidence:
                log.debug(
                    "Skipping '%s' (confidence %d < %d)",
                    c.title, c.confidence, min_confidence,
                )
                continue
            path = self.save(c)
            saved.append(path)

        return saved

    # ------------------------------------------------------------------
    # List / read
    # ------------------------------------------------------------------

    def list_notes(self) -> list[Path]:
        """Return all .md files in the knowledge base, sorted by mtime desc."""
        notes = list(self.root.rglob("*.md"))
        notes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return notes

    def count(self) -> int:
        return len(self.list_notes())
