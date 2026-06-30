"""
test_inject.py — Unit tests for inject.py.

No Ollama. No OpenClaw. No SQLite.
Uses fake SearchResult objects and a temporary directory.

Run with:
    python -m pytest tests/
    python tests/test_inject.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from index.vector_store import SearchResult
from inject import Injector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_result(
    title:    str   = "OpenClaw session layout",
    category: str   = "Configuration",
    score:    float = 0.94,
    summary:  str   = "Sessions are stored as JSONL files.",
    tags:     list  = None,
    confidence: int = 85,
    path:     str   = "/kb/note.md",
) -> SearchResult:
    return SearchResult(
        title      = title,
        category   = category,
        score      = score,
        summary    = summary,
        tags       = tags or ["openclaw", "session"],
        confidence = confidence,
        path       = path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInjector(unittest.TestCase):

    def setUp(self):
        self._tmp     = tempfile.mkdtemp()
        self.injector = Injector(context_dir=self._tmp)

    # ------------------------------------------------------------------
    # knowledge.md
    # ------------------------------------------------------------------

    def test_knowledge_file_created(self):
        result = self.injector.inject("test query", [_fake_result()])
        self.assertTrue(result.knowledge_path.exists())

    def test_knowledge_contains_title(self):
        result = self.injector.inject("test query", [_fake_result(title="OpenClaw session layout")])
        content = result.knowledge_path.read_text()
        self.assertIn("OpenClaw session layout", content)

    def test_knowledge_contains_query(self):
        result = self.injector.inject("What is the session directory?", [_fake_result()])
        content = result.knowledge_path.read_text()
        self.assertIn("What is the session directory?", content)

    def test_knowledge_contains_summary(self):
        result = self.injector.inject("q", [_fake_result(summary="Sessions stored as JSONL.")])
        content = result.knowledge_path.read_text()
        self.assertIn("Sessions stored as JSONL.", content)

    def test_knowledge_multiple_results(self):
        results = [
            _fake_result(title="Note A", path="/kb/a.md"),
            _fake_result(title="Note B", path="/kb/b.md"),
        ]
        result = self.injector.inject("query", results)
        content = result.knowledge_path.read_text()
        self.assertIn("Note A", content)
        self.assertIn("Note B", content)
        self.assertEqual(result.notes_written, 2)

    def test_knowledge_empty_results(self):
        result = self.injector.inject("query with no results", [])
        content = result.knowledge_path.read_text()
        self.assertIn("No relevant knowledge found", content)
        self.assertEqual(result.notes_written, 0)

    def test_knowledge_contains_score(self):
        result = self.injector.inject("q", [_fake_result(score=0.9321)])
        content = result.knowledge_path.read_text()
        self.assertIn("0.9321", content)

    def test_knowledge_contains_tags(self):
        result = self.injector.inject("q", [_fake_result(tags=["configuration", "openclaw"])])
        content = result.knowledge_path.read_text()
        self.assertIn("configuration", content)
        self.assertIn("openclaw", content)

    # ------------------------------------------------------------------
    # retrieval.json
    # ------------------------------------------------------------------

    def test_retrieval_file_created(self):
        result = self.injector.inject("q", [_fake_result()])
        self.assertTrue(result.retrieval_path.exists())

    def test_retrieval_is_valid_json(self):
        result = self.injector.inject("q", [_fake_result()])
        data = json.loads(result.retrieval_path.read_text())
        self.assertIsInstance(data, dict)

    def test_retrieval_contains_query(self):
        result = self.injector.inject("my query", [_fake_result()])
        data = json.loads(result.retrieval_path.read_text())
        self.assertEqual(data["query"], "my query")

    def test_retrieval_contains_results(self):
        results = [
            _fake_result(title="A", score=0.9, path="/kb/a.md"),
            _fake_result(title="B", score=0.8, path="/kb/b.md"),
        ]
        result = self.injector.inject("q", results)
        data   = json.loads(result.retrieval_path.read_text())
        self.assertEqual(data["top_k"], 2)
        self.assertEqual(data["results"][0]["title"], "A")
        self.assertEqual(data["results"][1]["title"], "B")
        self.assertEqual(data["results"][0]["rank"],  1)
        self.assertEqual(data["results"][1]["rank"],  2)

    def test_retrieval_contains_score(self):
        result = self.injector.inject("q", [_fake_result(score=0.875)])
        data   = json.loads(result.retrieval_path.read_text())
        self.assertAlmostEqual(data["results"][0]["score"], 0.875)

    def test_retrieval_empty_results(self):
        result = self.injector.inject("q", [])
        data   = json.loads(result.retrieval_path.read_text())
        self.assertEqual(data["top_k"], 0)
        self.assertEqual(data["results"], [])

    def test_retrieval_contains_generated_at(self):
        result = self.injector.inject("q", [_fake_result()])
        data   = json.loads(result.retrieval_path.read_text())
        self.assertIn("generated_at", data)
        self.assertIn("T", data["generated_at"])  # ISO 8601


if __name__ == "__main__":
    unittest.main(verbosity=2)
