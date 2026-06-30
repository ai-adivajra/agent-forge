#!/usr/bin/env python3
"""
build_index.py — Build the embedding index from the KnowledgeBase.

Reads all .md files in KnowledgeBase/, extracts YAML frontmatter,
embeds title + category + summary + tags via Ollama, and stores the
result in embeddings.sqlite.

Usage:
    python build_index.py              # full rebuild
    python build_index.py --check      # verify Ollama + KnowledgeBase, then exit
    python build_index.py --dry-run    # parse notes, skip embedding + writing
"""

import argparse
import logging
import sys
from pathlib import Path

import frontmatter  # python-frontmatter

from config import SETTINGS, expand
from index.embedder import Embedder
from index.vector_store import VectorStore
from ollama import OllamaError

log = logging.getLogger(__name__)

DIVIDER = "─" * 60


# ---------------------------------------------------------------------------
# Note loading
# ---------------------------------------------------------------------------

def load_notes(knowledge_dir: Path) -> list[dict]:
    """
    Parse all .md files in knowledge_dir recursively.
    Returns a list of note dicts with at minimum: path, title, category, summary.
    Silently skips files with missing or unreadable frontmatter.
    """
    notes: list[dict] = []

    for md_file in sorted(knowledge_dir.rglob("*.md")):
        try:
            post = frontmatter.load(str(md_file))
        except Exception as e:
            log.warning("Skipping %s: %s", md_file.name, e)
            continue

        title    = post.get("title",    "").strip()
        category = post.get("category", "").strip()
        summary  = post.get("summary",  "")

        # summary may be in frontmatter or in the body under a ## Summary heading
        if not summary:
            summary = _extract_summary_from_body(post.content)

        if not title or not category:
            log.debug("Skipping %s: missing title or category", md_file.name)
            continue

        tags = post.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        notes.append({
            "path":       str(md_file),
            "title":      title,
            "category":   category,
            "summary":    summary,
            "tags":       tags,
            "confidence": int(post.get("confidence", 0)),
            "domain":     str(post.get("domain",   "") or "").strip(),
            "platform":   str(post.get("platform", "") or "").strip(),
            "type":       str(post.get("type",     "") or "").strip(),
        })

    return notes


def _extract_summary_from_body(body: str) -> str:
    """
    Extract the first paragraph under a ## Summary heading.
    Returns empty string if not found.
    """
    lines  = body.splitlines()
    inside = False
    parts: list[str] = []

    for line in lines:
        if line.strip().lower() in ("## summary", "## résumé"):
            inside = True
            continue
        if inside:
            if line.startswith("##"):
                break
            if line.strip():
                parts.append(line.strip())

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_check(knowledge_dir: Path, embedder: Embedder, db_path: Path) -> None:
    print(DIVIDER)
    print("build_index — configuration check")
    print(DIVIDER)
    print(f"  KnowledgeBase  {knowledge_dir}")
    print(f"  Index DB       {db_path}")
    print(f"  Embed model    {embedder.model_name}")
    print()

    if not knowledge_dir.exists():
        print("  ✗  KnowledgeBase directory not found")
        sys.exit(1)

    notes = load_notes(knowledge_dir)
    print(f"  Notes found    {len(notes)}")

    if embedder.ping():
        print("  Ollama         ✓ reachable")
    else:
        print("  Ollama         ✗ NOT reachable")
        print("  → Run: systemctl --user start ollama")
        sys.exit(1)

    print(DIVIDER)


def cmd_build(
    knowledge_dir: Path,
    embedder:      Embedder,
    store:         VectorStore,
    dry_run:       bool = False,
) -> None:

    print(DIVIDER)
    print("Building index …")
    print(DIVIDER)
    print(f"  KnowledgeBase  {knowledge_dir}")
    print(f"  Embed model    {embedder.model_name}")
    print()

    # ---- Load notes ------------------------------------------------------
    notes = load_notes(knowledge_dir)
    print(f"  {len(notes)} note(s) found")
    print()

    if dry_run:
        print("  [DRY RUN] Parsing only — no embeddings generated, no DB written.")
        for n in notes:
            print(f"    {n['category']:<18} {n['title']}")
        return

    # ---- Sync: remove notes deleted from KnowledgeBase --------------------
    # The KnowledgeBase may have had notes removed since the last build.
    # Any path in the index that no longer exists on disk must be deleted.
    kb_paths = {str(n["path"]) for n in notes}
    with store._connect() as conn:
        indexed_paths = {
            row[0] for row in conn.execute("SELECT path FROM notes").fetchall()
        }
    stale = indexed_paths - kb_paths
    if stale:
        with store._connect() as conn:
            for p in stale:
                conn.execute("DELETE FROM notes WHERE path = ?", (p,))
        print(f"  Removed {len(stale)} stale entry/entries from index")
        print()

    if not notes:
        print("  ⚠ KnowledgeBase is empty — index cleared.")
        return

    # ---- Incremental index build ----------------------------------------
    # Only re-embed notes whose content has changed or whose schema is stale.
    ok = skipped = errors = 0

    for i, note in enumerate(notes, 1):
        label = note["title"][:45]

        # Build the text sent to the embedder — reads embed_fields from settings.yaml
        embed_fields = SETTINGS.get("index", {}).get(
            "embed_fields", ["title", "category", "summary", "tags"]
        )
        parts = []
        for f in embed_fields:
            v = note.get(f, "")
            if isinstance(v, list):
                v = " ".join(v)
            if v:
                parts.append(v)
        embed_text   = "\n".join(parts)
        content_hash = store.compute_hash(embed_text)

        if not store.needs_update(str(note["path"]), content_hash):
            print(f"  [{i:>3}/{len(notes)}]  {label:<46} — (unchanged)")
            skipped += 1
            continue

        print(f"  [{i:>3}/{len(notes)}]  {label:<46}", end="", flush=True)

        try:
            embedding = embedder.embed_note(note)
            store.add(note, embedding, content_hash, embedder.model_name)
            print(" ✓")
            ok += 1
        except OllamaError as e:
            print(f" ✗  {e}")
            errors += 1
        except Exception as e:
            print(f" ✗  unexpected: {e}")
            errors += 1

    if skipped:
        print(f"  ({skipped} note(s) unchanged — skipped)")

    # ---- Summary ---------------------------------------------------------
    print()
    stats = store.stats()
    print(DIVIDER)
    print(f"  Indexed        {ok} / {len(notes)} notes")
    if skipped:
        print(f"  Skipped        {skipped} (unchanged)")
    if errors:
        print(f"  Errors         {errors}")
    print()
    print(f"  Embedding dim  {stats.embedding_dim}")
    print(f"  DB size        {stats.db_size_bytes / 1024:.1f} KB")
    print(f"  DB path        {stats.db_path}")
    print()

    for cat, n in stats.by_category.items():
        print(f"    {cat:<20} {n}")

    print()
    if errors == 0:
        print("  ✓ Index complete — OK")
    else:
        print(f"  ⚠ Index complete with {errors} error(s)")

    print(DIVIDER)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description="Build the openclaw-knowledge embedding index."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify configuration and note count, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and list notes without generating embeddings or writing DB",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force full rebuild — drop all embeddings and re-embed every note",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

    # ---- Config ----------------------------------------------------------
    obs_cfg  = SETTINGS.get("obsidian", {})
    idx_cfg  = SETTINGS.get("index", {})

    vault_root    = expand(obs_cfg.get("vault", "~/Obsidian/AI/OpenClaw"))
    knowledge_dir = vault_root / obs_cfg.get("knowledge", "KnowledgeBase")
    db_path       = expand(idx_cfg.get("db", "~/.openclaw/embeddings.sqlite"))

    embedder = Embedder()
    store    = VectorStore(db_path)

    if args.check:
        cmd_check(knowledge_dir, embedder, db_path)
        return

    if args.rebuild:
        print("  [--rebuild] Dropping existing index …")
        store.rebuild()

    cmd_build(
        knowledge_dir = knowledge_dir,
        embedder      = embedder,
        store         = store,
        dry_run       = args.dry_run,
    )


if __name__ == "__main__":
    main()
