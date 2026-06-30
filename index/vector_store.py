"""
vector_store.py — The only file in the project that knows SQLite exists.

Contract:
    store = VectorStore(db_path)
    store.add(note, embedding, content_hash)  → None
    store.needs_update(path, content_hash)    → bool
    store.search(vector, top_k=5)             → list[SearchResult]
    store.stats()                             → StoreStats
    store.rebuild()                           → clears notes table, keeps meta
    store.get_meta(key)                       → str | None
    store.set_meta(key, value)                → None

If the backend ever changes (sqlite-vec, FAISS, pgvector), only this file
changes. search.py, build_index.py, and inject.py remain untouched.

Schema:

    notes — one row per indexed knowledge note
        id            INTEGER  PK
        path          TEXT     UNIQUE — absolute path to the .md file
        title         TEXT
        category      TEXT
        summary       TEXT
        tags          TEXT     JSON array
        confidence    INTEGER
        content_hash  TEXT     SHA256 of the text sent to the embedder
        embedding     TEXT     JSON float array
        embedding_model TEXT   name of the model used (e.g. nomic-embed-text)

    index_meta — key/value store for index-level metadata
        key           TEXT     PK
        value         TEXT

    Standard meta keys:
        embed_schema      integer version of the embedding recipe (bumped on change)
        embedding_model   last model used for a full build
"""

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# Bump this whenever the fields sent to the embedder change
# (title+category+domain+type+summary+tags = schema 2)
EMBED_SCHEMA_VERSION = "2"


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    path:            str
    title:           str
    category:        str
    summary:         str
    tags:            list[str]
    confidence:      int
    score:           float        # cosine similarity 0.0–1.0
    embedding_model: str = ""
    domain:          str = ""
    platform:        str = ""
    type:            str = ""


@dataclass
class StoreStats:
    total_notes:     int
    by_category:     dict[str, int]
    by_model:        dict[str, int]
    embedding_dim:   int | None
    embed_schema:    str | None
    db_size_bytes:   int
    db_path:         str


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:

    _CREATE_NOTES = """
        CREATE TABLE IF NOT EXISTS notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            path            TEXT    NOT NULL UNIQUE,
            title           TEXT    NOT NULL,
            category        TEXT    NOT NULL,
            summary         TEXT    NOT NULL DEFAULT '',
            tags            TEXT    NOT NULL DEFAULT '[]',
            confidence      INTEGER NOT NULL DEFAULT 0,
            content_hash    TEXT    NOT NULL DEFAULT '',
            embedding       TEXT    NOT NULL,
            embedding_model TEXT    NOT NULL DEFAULT '',
            domain          TEXT    NOT NULL DEFAULT '',
            platform        TEXT    NOT NULL DEFAULT '',
            type            TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_category ON notes(category);
        CREATE INDEX IF NOT EXISTS idx_hash     ON notes(content_hash);
        CREATE INDEX IF NOT EXISTS idx_domain   ON notes(domain);
    """

    _CREATE_META = """
        CREATE TABLE IF NOT EXISTS index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """

    _UPSERT = """
        INSERT INTO notes
            (path, title, category, summary, tags, confidence,
             content_hash, embedding, embedding_model, domain, platform, type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title           = excluded.title,
            category        = excluded.category,
            summary         = excluded.summary,
            tags            = excluded.tags,
            confidence      = excluded.confidence,
            content_hash    = excluded.content_hash,
            embedding       = excluded.embedding,
            embedding_model = excluded.embedding_model,
            domain          = excluded.domain,
            platform        = excluded.platform,
            type            = excluded.type;
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            # --- migrate BEFORE CREATE TABLE IF NOT EXISTS ---
            # The notes table may already exist with an older schema.
            # ALTER TABLE must run first; CREATE TABLE IF NOT EXISTS is a no-op
            # when the table already exists and will not add new columns.
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(notes)").fetchall()
            }
            if existing_cols:
                # Table exists — apply any missing columns
                if "content_hash" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE notes"
                        " ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
                    )
                if "embedding_model" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE notes"
                        " ADD COLUMN embedding_model TEXT NOT NULL DEFAULT ''"
                    )
                if "domain" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE notes"
                        " ADD COLUMN domain TEXT NOT NULL DEFAULT ''"
                    )
                if "platform" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE notes"
                        " ADD COLUMN platform TEXT NOT NULL DEFAULT ''"
                    )
                if "type" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE notes"
                        " ADD COLUMN type TEXT NOT NULL DEFAULT ''"
                    )
                conn.commit()

            # Now safe to run CREATE TABLE IF NOT EXISTS (no-op if table exists)
            conn.executescript(self._CREATE_NOTES)
            conn.executescript(self._CREATE_META)

        # Write schema version if not already present
        if self.get_meta("embed_schema") is None:
            self.set_meta("embed_schema", EMBED_SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO index_meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    def compute_hash(text: str) -> str:
        """SHA256 of the text sent to the embedder."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def needs_update(self, path: str, content_hash: str) -> bool:
        """
        Return True if the note at `path` is not in the index or its
        content_hash differs from the stored one.
        Always returns True if the embed_schema has changed.
        """
        stored_schema = self.get_meta("embed_schema")
        if stored_schema != EMBED_SCHEMA_VERSION:
            return True

        with self._connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM notes WHERE path = ?", (path,)
            ).fetchone()
        if row is None:
            return True
        return row["content_hash"] != content_hash

    def add(
        self,
        note:            dict,
        embedding:       list[float],
        content_hash:    str,
        embedding_model: str = "",
    ) -> None:
        """Insert or update one note with its embedding."""
        tags = note.get("tags", [])
        if isinstance(tags, list):
            tags = json.dumps(tags)

        with self._connect() as conn:
            conn.execute(self._UPSERT, (
                str(note["path"]),
                note.get("title",    ""),
                note.get("category", ""),
                note.get("summary",  ""),
                tags,
                int(note.get("confidence", 0)),
                content_hash,
                json.dumps(embedding),
                embedding_model,
                note.get("domain",   ""),
                note.get("platform", ""),
                note.get("type",     ""),
            ))

    def rebuild(self) -> None:
        """
        Drop and recreate the notes table.
        Preserves index_meta so schema version survives.
        """
        with self._connect() as conn:
            conn.execute("DROP TABLE IF EXISTS notes")
            conn.executescript(self._CREATE_NOTES)
        self.set_meta("embed_schema", EMBED_SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_vector: list[float],
        top_k:        int = 5,
        domains:      list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Brute-force cosine similarity over stored embeddings.
        Returns up to top_k results sorted by score descending.

        domains — if provided, restrict search to notes whose domain is in the list.
        """
        with self._connect() as conn:
            if domains:
                placeholders = ",".join("?" * len(domains))
                rows = conn.execute(
                    f"SELECT path, title, category, summary, tags, confidence,"
                    f"       embedding, embedding_model, domain, platform, type"
                    f" FROM notes WHERE domain IN ({placeholders})",
                    domains,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT path, title, category, summary, tags, confidence,"
                    "       embedding, embedding_model, domain, platform, type"
                    " FROM notes"
                ).fetchall()

        if not rows:
            return []

        q_norm = _norm(query_vector)
        if q_norm == 0:
            return []

        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vec   = json.loads(row["embedding"])
            score = _cosine(query_vector, vec, q_norm)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[SearchResult] = []
        for score, row in scored[:top_k]:
            tags = json.loads(row["tags"]) if row["tags"] else []
            results.append(SearchResult(
                path            = row["path"],
                title           = row["title"],
                category        = row["category"],
                summary         = row["summary"],
                tags            = tags,
                confidence      = row["confidence"],
                score           = round(score, 4),
                embedding_model = row["embedding_model"],
                domain          = row["domain"],
                platform        = row["platform"],
                type            = row["type"],
            ))

        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> StoreStats:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

            by_cat = {}
            for row in conn.execute(
                "SELECT category, COUNT(*) n FROM notes"
                " GROUP BY category ORDER BY n DESC"
            ).fetchall():
                by_cat[row["category"]] = row["n"]

            by_model = {}
            for row in conn.execute(
                "SELECT embedding_model, COUNT(*) n FROM notes"
                " GROUP BY embedding_model ORDER BY n DESC"
            ).fetchall():
                by_model[row["embedding_model"] or "(unknown)"] = row["n"]

            dim = None
            first = conn.execute("SELECT embedding FROM notes LIMIT 1").fetchone()
            if first and first["embedding"]:
                dim = len(json.loads(first["embedding"]))

        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        return StoreStats(
            total_notes   = total,
            by_category   = by_cat,
            by_model      = by_model,
            embedding_dim = dim,
            embed_schema  = self.get_meta("embed_schema"),
            db_size_bytes = db_size,
            db_path       = str(self.db_path),
        )


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine(a: list[float], b: list[float], a_norm: float) -> float:
    if len(a) != len(b):
        return 0.0
    b_norm = _norm(b)
    if b_norm == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (a_norm * b_norm)
