"""SQLite-backed Vault storage for PII entities, surfaces, and markers (role 3, issue #11).

Path is project-scoped and isolated outside the workspace.
Permissions on POSIX are set to 0600 (owner read/write only).
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pii_gateway.core.types import PiiType, VaultError

SAFE_PROJECT_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def get_vault_dir() -> Path:
    """Return directory for SQLite vault databases outside the workspace."""
    override = os.environ.get("PII_VAULT_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            path = Path(base) / "pii-gateway" / "vault"
        else:
            path = Path.home() / ".local" / "share" / "pii-gateway" / "vault"
    else:
        path = Path.home() / ".local" / "share" / "pii-gateway" / "vault"

    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return path


def get_vault_path(project_id: str) -> Path:
    """Return database file path for project_id, ensuring no path traversal."""
    if not project_id or not SAFE_PROJECT_RE.match(project_id):
        raise VaultError(f"invalid project_id '{project_id}': must match [a-zA-Z0-9_.-]+")
    return get_vault_dir() / f"{project_id}.db"


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    project_id: str
    pii_type: PiiType
    is_ambiguous: bool
    created_at: float


@dataclass(frozen=True)
class SurfaceRecord:
    surface_id: str
    entity_id: str
    side: str  # 'original', 'pseudo', or 'canonical'
    form: str | None
    text: str
    raw_hash: str | None


class Vault:
    """Project-scoped SQLite storage for PII mappings and audit events."""

    def __init__(self, project_id: str, db_path: Path | None = None) -> None:
        self.project_id = project_id
        self.db_path = db_path or get_vault_path(project_id)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                isolation_level="DEFERRED",
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 30000;")
            if os.name != "nt" and self.db_path.exists():
                try:
                    self.db_path.chmod(0o600)
                except OSError:
                    pass
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    pii_type TEXT NOT NULL,
                    is_ambiguous INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS surfaces (
                    surface_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    form TEXT,
                    text TEXT NOT NULL,
                    raw_hash TEXT,
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_surfaces
                    ON surfaces(entity_id, side, text, COALESCE(form, ''));
                CREATE INDEX IF NOT EXISTS idx_surfaces_lookup ON surfaces(side, text);
                CREATE INDEX IF NOT EXISTS idx_surfaces_entity ON surfaces(entity_id);

                CREATE TABLE IF NOT EXISTS mentions (
                    mention_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    start_idx INTEGER NOT NULL,
                    end_idx INTEGER NOT NULL,
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_mentions
                    ON mentions(entity_id, source_id, start_idx, end_idx);

                CREATE TABLE IF NOT EXISTS markers (
                    marker_text TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    pii_type TEXT NOT NULL,
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_markers_entity ON markers(entity_id);

                CREATE TABLE IF NOT EXISTS counters (
                    pii_type TEXT PRIMARY KEY,
                    next_val INTEGER NOT NULL
                );

                -- engine lookup tables: normalized key -> entity, replacement -> original
                CREATE TABLE IF NOT EXISTS entity_keys (
                    key TEXT PRIMARY KEY,
                    entity_id TEXT,  -- NULL: key shared by several entities (ambiguous)
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pairs (
                    replacement TEXT PRIMARY KEY,
                    original TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    event TEXT NOT NULL,
                    entity_id TEXT,
                    details TEXT
                );
                """
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def purge(self) -> None:
        """Purge all records in this vault."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM audit;")
            conn.execute("DELETE FROM counters;")
            conn.execute("DELETE FROM markers;")
            conn.execute("DELETE FROM mentions;")
            conn.execute("DELETE FROM surfaces;")
            conn.execute("DELETE FROM entities;")

    def get_next_marker_index(self, pii_type: str | PiiType) -> int:
        """Atomically increment and return the next marker index for pii_type."""
        type_str = str(pii_type)
        conn = self._get_connection()
        with conn:
            # Atomic upsert with RETURNING guarantees no race condition across threads/connections
            row = conn.execute(
                """
                INSERT INTO counters (pii_type, next_val) VALUES (?, 1)
                ON CONFLICT(pii_type) DO UPDATE SET next_val = counters.next_val + 1
                RETURNING next_val;
                """,
                (type_str,),
            ).fetchone()
            return int(row[0])

    def reserve_marker_index_at_least(self, pii_type: str | PiiType, last_taken: int) -> None:
        """Ensure counter for pii_type has recorded last_taken as already used."""
        type_str = str(pii_type)
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO counters (pii_type, next_val) VALUES (?, ?)
                ON CONFLICT(pii_type) DO UPDATE SET next_val = MAX(counters.next_val, ?)
                """,
                (type_str, last_taken, last_taken),
            )

    def add_entity(
        self,
        entity_id: str,
        pii_type: PiiType,
        is_ambiguous: bool = False,
    ) -> EntityRecord:
        """Create and store a new entity."""
        conn = self._get_connection()
        rec = EntityRecord(
            entity_id=entity_id,
            project_id=self.project_id,
            pii_type=pii_type,
            is_ambiguous=is_ambiguous,
            created_at=time.time(),
        )
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO entities(
                    entity_id, project_id, pii_type, is_ambiguous, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rec.entity_id,
                    rec.project_id,
                    str(rec.pii_type),
                    1 if rec.is_ambiguous else 0,
                    rec.created_at,
                ),
            )
        return rec

    def add_surface(
        self,
        surface_id: str,
        entity_id: str,
        side: str,
        text: str,
        form: str | None = None,
        raw_hash: str | None = None,
    ) -> None:
        """Store a surface mapping idempotently."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO surfaces(surface_id, entity_id, side, form, text, raw_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, side, text, COALESCE(form, '')) DO NOTHING
                """,
                (surface_id, entity_id, side, form, text, raw_hash),
            )

    def add_marker(self, marker_text: str, entity_id: str, pii_type: PiiType) -> None:
        """Store a marker mapping idempotently without overwriting."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO markers(marker_text, entity_id, pii_type)
                VALUES (?, ?, ?)
                ON CONFLICT(marker_text) DO NOTHING
                """,
                (marker_text, entity_id, str(pii_type)),
            )

    def add_mention(
        self,
        mention_id: str,
        entity_id: str,
        source_id: str,
        start_idx: int,
        end_idx: int,
    ) -> None:
        """Store a mention occurrence idempotently."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO mentions(mention_id, entity_id, source_id, start_idx, end_idx)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, source_id, start_idx, end_idx) DO NOTHING
                """,
                (mention_id, entity_id, source_id, start_idx, end_idx),
            )

    def find_entity_by_original_text(
        self, text: str, pii_type: PiiType | None = None
    ) -> EntityRecord | None:
        """Find an existing entity by exact matching original or canonical surface text."""
        conn = self._get_connection()
        if pii_type is not None:
            query = """
                SELECT e.entity_id, e.project_id, e.pii_type, e.is_ambiguous, e.created_at
                FROM entities e
                JOIN surfaces s ON s.entity_id = e.entity_id
                WHERE s.side IN ('original', 'canonical') AND s.text = ? AND e.pii_type = ?
                LIMIT 1
            """
            row = conn.execute(query, (text, str(pii_type))).fetchone()
        else:
            query = """
                SELECT e.entity_id, e.project_id, e.pii_type, e.is_ambiguous, e.created_at
                FROM entities e
                JOIN surfaces s ON s.entity_id = e.entity_id
                WHERE s.side IN ('original', 'canonical') AND s.text = ?
                LIMIT 1
            """
            row = conn.execute(query, (text,)).fetchone()

        if row is None:
            return None
        return EntityRecord(
            entity_id=row["entity_id"],
            project_id=row["project_id"],
            pii_type=PiiType(row["pii_type"]),
            is_ambiguous=bool(row["is_ambiguous"]),
            created_at=float(row["created_at"]),
        )

    def find_original_by_marker(self, marker_text: str) -> str | None:
        """Return the original surface text corresponding to the marker."""
        conn = self._get_connection()
        query = """
            SELECT s.text
            FROM surfaces s
            JOIN markers m ON m.entity_id = s.entity_id
            WHERE m.marker_text = ? AND s.side IN ('original', 'canonical')
            ORDER BY CASE WHEN s.side = 'original' THEN 0 ELSE 1 END, s.rowid ASC
            LIMIT 1
        """
        row = conn.execute(query, (marker_text,)).fetchone()
        return str(row["text"]) if row else None

    def get_marker_for_entity(self, entity_id: str) -> str | None:
        """Return stored marker text for entity_id if any."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT marker_text FROM markers WHERE entity_id = ? LIMIT 1", (entity_id,)
        ).fetchone()
        return str(row["marker_text"]) if row else None

    def get_marker_for_surface(self, entity_id: str, original: str) -> str | None:
        """Reuse a marker only when both entity and exact original surface match."""
        row = self._get_connection().execute(
            "SELECT m.marker_text FROM markers m JOIN pairs p "
            "ON p.replacement = m.marker_text AND p.entity_id = m.entity_id "
            "WHERE m.entity_id = ? AND p.original = ? ORDER BY m.marker_text LIMIT 1",
            (entity_id, original),
        ).fetchone()
        return str(row["marker_text"]) if row else None

    def get_all_marker_mappings(self) -> dict[str, str]:
        """Return a dictionary of {marker_text: canonical_original_text} for this project."""
        conn = self._get_connection()
        query = """
            SELECT m.marker_text,
                   (SELECT s.text FROM surfaces s
                    WHERE s.entity_id = m.entity_id AND s.side IN ('original', 'canonical')
                    ORDER BY CASE WHEN s.side = 'original' THEN 0 ELSE 1 END, s.rowid ASC
                    LIMIT 1) as orig_text
            FROM markers m
        """
        rows = conn.execute(query).fetchall()
        return {
            str(r["marker_text"]): str(r["orig_text"])
            for r in rows
            if r["orig_text"] is not None
        }

    def get_key(self, key: str) -> tuple[bool, str | None]:
        """(found, entity_id); entity_id is None for an ambiguous key."""
        row = self._get_connection().execute(
            "SELECT entity_id FROM entity_keys WHERE key = ?", (key,)
        ).fetchone()
        return (False, None) if row is None else (True, row["entity_id"])

    def add_key(self, key: str, entity_id: str) -> None:
        """Register key; a key already owned by another entity becomes ambiguous."""
        conn = self._get_connection()
        with conn:
            row = conn.execute("SELECT entity_id FROM entity_keys WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO entity_keys(key, entity_id) VALUES (?, ?)", (key, entity_id)
                )
            elif row["entity_id"] not in (None, entity_id):
                conn.execute("UPDATE entity_keys SET entity_id = NULL WHERE key = ?", (key,))

    def get_pair(self, replacement: str) -> str | None:
        row = self._get_connection().execute(
            "SELECT original FROM pairs WHERE replacement = ?", (replacement,)
        ).fetchone()
        return None if row is None else str(row["original"])

    def add_pair(self, replacement: str, original: str, entity_id: str) -> None:
        """Never silently accept a replacement already bound to another value."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO pairs(replacement, original, entity_id) VALUES (?, ?, ?)",
                (replacement, original, entity_id),
            )
            row = conn.execute(
                "SELECT original, entity_id FROM pairs WHERE replacement = ?", (replacement,)
            ).fetchone()
            if row["original"] != original or row["entity_id"] != entity_id:
                raise VaultError("replacement collision")

    def get_pairs(self) -> dict[str, str]:
        rows = self._get_connection().execute("SELECT replacement, original FROM pairs").fetchall()
        return {str(r["replacement"]): str(r["original"]) for r in rows}

    def is_known_original(self, text: str) -> bool:
        row = self._get_connection().execute(
            "SELECT 1 FROM pairs WHERE original = ? LIMIT 1", (text,)
        ).fetchone()
        return row is not None

    def get_all_pseudo_mappings(self) -> list[tuple[str, str, str | None]]:
        """Return list of (pseudo_text, original_text, form) sorted by descending pseudo length."""
        conn = self._get_connection()
        query = """
            SELECT p.text as pseudo_text, o.text as orig_text, p.form
            FROM surfaces p
            JOIN surfaces o ON o.entity_id = p.entity_id AND o.side = 'original'
            WHERE p.side = 'pseudo' AND (p.form = o.form OR (p.form IS NULL AND o.form IS NULL))
        """
        rows = conn.execute(query).fetchall()
        result = [
            (str(r["pseudo_text"]), str(r["orig_text"]), r["form"])
            for r in rows
        ]
        result.sort(key=lambda t: len(t[0]), reverse=True)
        return result

    def get_all_used_pseudonyms(self) -> set[str]:
        """Return set of all pseudonym texts allocated in this project."""
        conn = self._get_connection()
        rows = conn.execute("SELECT text FROM surfaces WHERE side = 'pseudo'").fetchall()
        return {str(r["text"]) for r in rows}

    def count_rows(self) -> dict[str, int]:
        """Diagnostic count of rows in all tables."""
        conn = self._get_connection()
        counts = {}
        for table in ("entities", "surfaces", "mentions", "markers", "counters"):
            c = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = int(c)
        return counts
