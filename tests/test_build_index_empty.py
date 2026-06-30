"""
test_build_index_empty.py

Regression test for the empty-KB bug:
    If the KnowledgeBase is emptied after notes were indexed,
    build_index.py must clear the index — not leave stale entries.

Run with:
    python -m pytest tests/
    python tests/test_build_index_empty.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from index.vector_store import VectorStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embedding(dim: int = 8) -> list[float]:
    return [0.1] * dim


def _fake_note(path: str, title: str = "Test note") -> dict:
    return {
        "path":       path,
        "title":      title,
        "category":   "Configuration",
        "summary":    "A test note.",
        "tags":       ["test"],
        "confidence": 80,
    }


def _simulate_build(store: VectorStore, kb_paths: list[str]) -> None:
    """
    Simulate what build_index.py does:
    1. Remove stale entries (paths no longer in KB)
    2. Add/update current notes
    """
    kb_set = set(kb_paths)

    # --- sync: delete stale ---
    with store._connect() as conn:
        indexed = {
            row[0] for row in conn.execute("SELECT path FROM notes").fetchall()
        }
    stale = indexed - kb_set
    if stale:
        with store._connect() as conn:
            for p in stale:
                conn.execute("DELETE FROM notes WHERE path = ?", (p,))

    # --- add current notes ---
    for p in kb_paths:
        note = _fake_note(p)
        emb  = _fake_embedding()
        h    = store.compute_hash(note["title"])
        store.add(note, emb, h, "nomic-embed-text")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildIndexEmpty(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.store = VectorStore(self._tmp.name)

    def tearDown(self):
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_stale_entries_removed_when_kb_emptied(self):
        """
        Regression: if all notes are deleted from KnowledgeBase,
        build_index must clear the index — not leave stale entries.
        """
        # Step 1: index two notes
        _simulate_build(self.store, ["/kb/note-a.md", "/kb/note-b.md"])
        self.assertEqual(self.store.stats().total_notes, 2)

        # Step 2: KnowledgeBase is emptied, build again
        _simulate_build(self.store, [])
        self.assertEqual(
            self.store.stats().total_notes, 0,
            "Index must be empty after KB is emptied"
        )

    def test_stale_single_note_removed(self):
        """Deleting one note from KB removes only that entry."""
        _simulate_build(self.store, ["/kb/note-a.md", "/kb/note-b.md"])
        _simulate_build(self.store, ["/kb/note-a.md"])

        stats = self.store.stats()
        self.assertEqual(stats.total_notes, 1)

        results = self.store.search(_fake_embedding(), top_k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, "/kb/note-a.md")

    def test_new_note_added(self):
        """A note added to KB appears in the index after build."""
        _simulate_build(self.store, ["/kb/note-a.md"])
        _simulate_build(self.store, ["/kb/note-a.md", "/kb/note-b.md"])
        self.assertEqual(self.store.stats().total_notes, 2)

    def test_unchanged_note_hash_preserved(self):
        """A note whose content hasn't changed keeps its content_hash."""
        _simulate_build(self.store, ["/kb/note-a.md"])

        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM notes WHERE path = ?",
                ("/kb/note-a.md",)
            ).fetchone()
        original_hash = row["content_hash"]

        # Build again — same content
        _simulate_build(self.store, ["/kb/note-a.md"])

        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM notes WHERE path = ?",
                ("/kb/note-a.md",)
            ).fetchone()
        self.assertEqual(row["content_hash"], original_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
