# AetherMind / AMind — Architecture Notes

Source: private projects (aethermind-kh/AetherMind, aethermind-kh/Amind)
Deleted from disk: 2026-07-02
Code preserved on GitHub.

## Vocabulary comparison

AetherMind: Enhance → Learn → Deduplicate → Rate  
Agent Forge: Capture → Retrieve → Prime → Investigate

AetherMind is platform-centric. Agent Forge is workflow-centric.

---

## learned_process_builder.py ★★★★★

Core idea: scan KB for recurring patterns → synthesize a playbook with citations.

Directly analogous to the BEHAVIOR.md promotion mechanism:
  capture.py → N similar incidents → behavior candidate → review → BEHAVIOR.md

The pipeline (candidates → dedup steps → citations → indexed document) is
exactly what a future synthesize.py would implement in Agent Forge.

Do NOT reuse code directly (hard dependency on LlamaIndex + Chroma).
Reuse the pattern.

---

## smart_rating_feedback_system.py ★★★★★

Core idea: not the rating UI itself, but the filtering philosophy:
  many events → threshold → only interesting events surface for human review

Agent Forge already does this:
  capture.py → confidence threshold → knowledge candidates → KB

Future extension: a "candidate knowledge queue" or "candidate behavior queue"
with configurable thresholds, cleanup, and human review prioritization.
Concepts worth borrowing: configurable thresholds, bounded storage,
review queue, auto-cleanup of stale candidates.

---

## corag_enhancements.py ★★★★☆

HierarchicalChunker: document → section → chunk (with section_index,
chunk_index, structural_position, hierarchical_context).
Current Agent Forge indexing is flat. This would improve build_index.py
to respect document structure — relevant when KB notes grow longer.

Rename suggestion: StructuredChunker (drop CoRAG brand, stay agnostic).

MetadataEnhancer: adds technical_level, document_complexity,
multimodal_indicators, content_structure. Some dimensions are superfluous
for Agent Forge's current scale; revisit when KB exceeds ~100 notes.

---

## persistent_deduplication.py ★★★★☆

Good: persistent registry, hash, thread-safe lock, statistics.
Limitation: MD5 + filename — purely syntactic, not semantic.

For Agent Forge the right deduplication criterion is semantic:
  same incident? same knowledge? near-duplicate? same text?

Keep the architecture (registry, locks, stats), replace the criterion
with embedding similarity when deduplication becomes a real problem.

---

## What was NOT worth keeping

- EnhancedTableProcessor: overkill for markdown KB notes
- Redis/Celery architecture: wrong scale for a local tool
- Streamlit UI: Agent Forge is deliberately headless
- CoRAG branding: creates unnecessary dependency on a specific paper
