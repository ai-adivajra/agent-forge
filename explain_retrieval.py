#!/usr/bin/env python3
"""
explain_retrieval.py — Diagnose why a query retrieves the notes it does.

Single responsibility: question → full transparency on every note's score,
matched terms, and why it was or wasn't included in the top results.

v2: decomposes the final score into embedding similarity + lexical overlap,
and shows WHERE each matched term was found (title/summary/tags). This is
a measurement/diagnostic tool only — it does not change search.py's actual
ranking. Use --rerank to preview what a hybrid score WOULD look like.

Does not modify search.py, vector_store.py, or inject.py.

Usage:
    python explain_retrieval.py "How do I fix ROCm on Fedora?"
    python explain_retrieval.py "skill_workshop apply" --top 10
    python explain_retrieval.py "vague query" --show-all
    python explain_retrieval.py "skill_workshop apply" --rerank
    python explain_retrieval.py "skill_workshop apply" --rerank --lexical-weight 0.3
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib     import Path

sys.path.insert(0, str(Path(__file__).parent))

from config              import SETTINGS, expand
from index.embedder      import Embedder
from index.vector_store  import VectorStore

DIVIDER = "─" * 70

STOPWORDS = {
    "the", "a", "an", "is", "are", "do", "does", "did", "how", "what",
    "why", "when", "where", "to", "of", "in", "on", "for", "and", "or",
    "i", "my", "me", "can", "could", "would", "should", "with", "this",
    "that", "it", "its", "fix", "issue", "problem",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9_:./-]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


@dataclass
class FieldMatches:
    title:   set[str] = field(default_factory=set)
    summary: set[str] = field(default_factory=set)
    tags:    set[str] = field(default_factory=set)

    @property
    def all_matched(self) -> set[str]:
        return self.title | self.summary | self.tags

    def location_str(self) -> str:
        locs = []
        if self.title:   locs.append("title")
        if self.summary: locs.append("summary")
        if self.tags:    locs.append("tags")
        return ", ".join(locs) if locs else "—"


def _field_matches(query_tokens: set[str], r) -> FieldMatches:
    return FieldMatches(
        title   = query_tokens & _tokenize(r.title),
        summary = query_tokens & _tokenize(r.summary),
        tags    = query_tokens & _tokenize(" ".join(r.tags)),
    )


def _lexical_score(matched: set[str], query_tokens: set[str]) -> float:
    """Fraction of query tokens found anywhere in the note. 0.0–1.0."""
    if not query_tokens:
        return 0.0
    return len(matched) / len(query_tokens)


def explain(
    question:       str,
    top_k:          int,
    show_all:       bool,
    cutoff_fraction: float,
    rerank:         bool,
    lexical_weight: float,
) -> None:
    idx_cfg = SETTINGS.get("index", {})
    db_path = expand(idx_cfg.get("db", "~/.openclaw/embeddings.sqlite"))

    if not db_path.exists():
        print(f"  ✗  Index not found at {db_path}")
        print("     Run: python build_index.py")
        sys.exit(1)

    store    = VectorStore(db_path)
    embedder = Embedder()

    if not embedder.ping():
        print("  ✗  Ollama is not reachable. Run: systemctl --user start ollama")
        sys.exit(1)

    print(DIVIDER)
    print("explain_retrieval.py — retrieval diagnostic")
    print(DIVIDER)
    print(f"  Query : {question}")

    query_vector = embedder.embed(question)
    query_tokens = _tokenize(question)
    print(f"  Query terms (after stopword removal): {sorted(query_tokens)}")
    if rerank:
        print(f"  Rerank preview: final = {1-lexical_weight:.0%} embedding + {lexical_weight:.0%} lexical")
    print()

    all_results = store.search(query_vector, top_k=10_000)

    if not all_results:
        print("  No notes in the index.")
        return

    # Compute field matches + lexical score for every result up front
    enriched = []
    for r in all_results:
        fm      = _field_matches(query_tokens, r)
        lexical = _lexical_score(fm.all_matched, query_tokens)
        final   = (
            (1 - lexical_weight) * r.score + lexical_weight * lexical
            if rerank else r.score
        )
        enriched.append((final, r, fm, lexical))

    if rerank:
        enriched.sort(key=lambda x: x[0], reverse=True)

    top_score = enriched[0][0]
    cutoff    = top_score * cutoff_fraction

    print(f"  {len(enriched)} note(s) in index")
    print(f"  Top score: {top_score:.4f}  |  Cutoff used by prime.py: {cutoff:.4f} "
          f"({cutoff_fraction:.0%} of top)")
    print()

    display_set = enriched if show_all else enriched[:max(top_k, 10)]

    print(DIVIDER)
    header = "  Rank  Final   Embed   Lex   In-top-K  Above-cut  Title" if rerank else \
             "  Rank  Score   Domain        In-top-K  Above-cut  Title"
    print(header)
    print(DIVIDER)

    for i, (final, r, fm, lexical) in enumerate(display_set, 1):
        in_top    = "✓" if i <= top_k else " "
        above_cut = "✓" if final >= cutoff else " "
        if rerank:
            print(f"  {i:>4}  {final:.4f}  {r.score:.4f}  {lexical:.2f}    "
                  f"  {in_top}        {above_cut}        {r.title[:40]}")
        else:
            domain = (r.domain or "general")[:12].ljust(12)
            print(f"  {i:>4}  {final:.4f}  {domain}  "
                  f"     {in_top}          {above_cut}        {r.title[:45]}")

    print(DIVIDER)
    print()

    print("DETAIL — score breakdown and match location per note")
    print(DIVIDER)

    for i, (final, r, fm, lexical) in enumerate(display_set, 1):
        matched = fm.all_matched
        missed  = query_tokens - matched

        status = []
        if i <= top_k:
            status.append("in top-K")
        if final >= cutoff:
            status.append("above cutoff")
        if not status:
            status.append("excluded")

        print(f"\n  [{i}] {r.title}")
        if rerank:
            print(f"      Final score : {final:.4f}  =  "
                  f"embedding {r.score:.4f} × {1-lexical_weight:.0%}  +  "
                  f"lexical {lexical:.2f} × {lexical_weight:.0%}")
        else:
            print(f"      Score    : {r.score:.4f}   Status: {', '.join(status)}")
        print(f"      Domain   : {r.domain or 'general'}  ·  Type: {r.type or 'general'}  "
              f"·  Category: {r.category}")
        if matched:
            print(f"      Matched query terms  : {sorted(matched)}")
            print(f"      Found in fields      : {fm.location_str()}")
        if missed:
            print(f"      Query terms NOT found : {sorted(missed)}")
        if not matched and not missed:
            print(f"      No literal term overlap — score is purely semantic (embedding similarity)")

    print()
    print(DIVIDER)
    print("READING THIS REPORT")
    print(DIVIDER)
    print("""
  High embedding + high lexical    → strong match, both signals agree
  High embedding + zero lexical    → pure semantic match — verify it's
                                      actually relevant, not just
                                      "same general topic"
  Low embedding + high lexical     → note mentions the right words but
                                      embedding undervalues it — check
                                      if --rerank changes the ranking
  Use --rerank to preview how a hybrid score would reorder results
  without changing the live search.py ranking.
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explain why a query retrieves the notes it does.",
    )
    parser.add_argument("question", help="The search query to explain")
    parser.add_argument("--top", type=int, default=5, metavar="K",
                        help="Top-K to highlight as 'in top-K' (default: 5)")
    parser.add_argument("--show-all", action="store_true",
                        help="Show every note in the index, not just the top results")
    parser.add_argument("--cutoff", type=float, default=0.55, metavar="FRAC",
                        help="Score cutoff fraction (default: 0.55)")
    parser.add_argument("--rerank", action="store_true",
                        help="Preview a hybrid embedding+lexical score (diagnostic only, "
                             "does not change search.py)")
    parser.add_argument("--lexical-weight", type=float, default=0.2, metavar="W",
                        help="Weight of lexical overlap in --rerank preview (default: 0.2)")

    args = parser.parse_args()
    explain(args.question, args.top, args.show_all, args.cutoff,
            args.rerank, args.lexical_weight)


if __name__ == "__main__":
    main()
