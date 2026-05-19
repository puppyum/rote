"""Layer 3b — SQLite index + blob filesystem.

* ``<cache_dir>/index.db`` — SQLite (WAL mode) with one row per cache entry
* ``<cache_dir>/blobs/<first-2-hex>/<rest>.bin`` — payload files

All writes are atomic: tempfile + ``os.replace``. Directory fsync on POSIX.
Safe for concurrent access from multiple processes.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key BLOB PRIMARY KEY,
    function_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    serializer TEXT NOT NULL,
    file_dependencies TEXT NOT NULL,
    file_dep_hash BLOB,
    file_write_dependencies TEXT NOT NULL DEFAULT '[]',
    code_dependencies TEXT NOT NULL,
    run_duration_ns INTEGER NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    last_hit_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_entries_func ON entries(function_name);
CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at);
"""

# Backwards-compat for older databases — idempotent ALTER TABLE.
SCHEMA_MIGRATE = (
    "ALTER TABLE entries ADD COLUMN file_dep_hash BLOB",
    "ALTER TABLE entries ADD COLUMN file_write_dependencies TEXT NOT NULL DEFAULT '[]'",
)


@dataclass
class Entry:
    key: bytes
    function_name: str
    created_at: int
    size_bytes: int
    serializer: str
    file_dependencies: list[str] = field(default_factory=list)
    file_dep_hash: bytes | None = None
    file_write_dependencies: list[str] = field(default_factory=list)
    code_dependencies: list[str] = field(default_factory=list)
    run_duration_ns: int = 0
    hits: int = 0
    last_hit_at: int | None = None


# When the deferred-hit buffer grows past this, ``hit()`` triggers an
# auto-flush. Caps the per-process memory cost of lazy hit counters
# even when the user never calls ``close()`` (Jupyter kernels, daemons).
_PENDING_HITS_FLUSH_AT = 1024


class Store:
    """Atomic, concurrent-safe cache store."""

    def __init__(self, cache_dir: Path, *, fsync_writes: bool = True) -> None:
        self.cache_dir: Path = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._blobs_dir: Path = self.cache_dir / "blobs"
        self._blobs_dir.mkdir(parents=True, exist_ok=True)
        # Pre-resolve to a string so blob-path construction stays in str-land.
        self._blobs_root: str = str(self._blobs_dir)
        self.db_path: Path = self.cache_dir / "index.db"
        self._conn: sqlite3.Connection | None = None
        self.fsync_writes: bool = fsync_writes
        # Buffer of keys for lazy hit-counter updates. Flushed automatically
        # when the buffer reaches _PENDING_HITS_FLUSH_AT or when close() is
        # called — without the size trigger, a long-lived Store (e.g. a
        # Jupyter kernel) would grow this list one entry per cache hit
        # forever.
        self._pending_hits: list[bytes] = []
        self._open_db()

    # ----- db lifecycle

    def _open_db(self) -> None:
        # ``check_same_thread=False`` is safe because we serialize via SQLite's
        # own locking, not Python-level. We hold the connection per-Store.
        conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        # busy_timeout MUST be set before journal_mode=WAL — the WAL pragma
        # takes a brief exclusive lock and immediately fails ("database is
        # locked") if another process is mid-write. With busy_timeout, SQLite
        # spins until the lock clears.
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Bigger cache + memory-mapped reads further reduce per-query overhead.
        conn.execute("PRAGMA cache_size=-20000")  # 20 MB page cache
        conn.execute("PRAGMA mmap_size=268435456")  # 256 MB mmap
        conn.executescript(SCHEMA)
        # Backwards-compat for older DBs that lack file_dep_hash.
        for stmt in SCHEMA_MIGRATE:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(stmt)
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self.flush_hits()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- blob path layout

    def _blob_path(self, key: bytes) -> Path:
        # Hot path: avoid pathlib's __truediv__ chain (3 Path allocations + __fspath__).
        hexkey = key.hex()
        return Path(f"{self._blobs_root}/{hexkey[:2]}/{hexkey[2:]}.bin")

    def _blob_path_str(self, key: bytes) -> str:
        """String form of the blob path — avoids the Path() allocation."""
        hexkey = key.hex()
        return f"{self._blobs_root}/{hexkey[:2]}/{hexkey[2:]}.bin"

    # ----- writes

    def put(
        self,
        key: bytes,
        function_name: str,
        serializer: str,
        payload: bytes,
        file_dependencies: Iterable[str] = (),
        file_dep_hash: bytes | None = None,
        file_write_dependencies: Iterable[str] = (),
        code_dependencies: Iterable[str] = (),
        run_duration_ns: int = 0,
    ) -> Entry:
        """Atomically write a payload + index row. Returns the persisted Entry."""
        path = self._blob_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, payload)
        now = int(time.time())
        entry = Entry(
            key=key,
            function_name=function_name,
            created_at=now,
            size_bytes=len(payload),
            serializer=serializer,
            file_dependencies=list(file_dependencies),
            file_dep_hash=file_dep_hash,
            file_write_dependencies=list(file_write_dependencies),
            code_dependencies=list(code_dependencies),
            run_duration_ns=run_duration_ns,
        )
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(key, function_name, created_at, size_bytes, serializer, "
            "file_dependencies, file_dep_hash, file_write_dependencies, "
            "code_dependencies, run_duration_ns, hits, last_hit_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT hits FROM entries WHERE key=?), 0), NULL)",
            (
                entry.key,
                entry.function_name,
                entry.created_at,
                entry.size_bytes,
                entry.serializer,
                json.dumps(entry.file_dependencies),
                entry.file_dep_hash,
                json.dumps(entry.file_write_dependencies),
                json.dumps(entry.code_dependencies),
                entry.run_duration_ns,
                entry.key,
            ),
        )
        return entry

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        # Write to a temp file in the SAME directory so os.replace is atomic
        # across filesystems (rename across mounts is not atomic).
        fd, tmp = tempfile.mkstemp(
            prefix=".tmp.", suffix=".bin", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                if self.fsync_writes:
                    f.flush()
                    with contextlib.suppress(OSError):
                        os.fsync(f.fileno())
            os.replace(tmp, target)
            # Directory fsync only when durability requested (saves ~500µs/write).
            if self.fsync_writes and sys.platform != "win32":
                try:
                    dfd = os.open(str(target.parent), os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except OSError:
                    pass
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ----- reads

    def get_entry(self, key: bytes) -> Entry | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT key, function_name, created_at, size_bytes, serializer, "
            "file_dependencies, file_dep_hash, file_write_dependencies, "
            "code_dependencies, run_duration_ns, hits, last_hit_at "
            "FROM entries WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return Entry(
            key=row[0],
            function_name=row[1],
            created_at=row[2],
            size_bytes=row[3],
            serializer=row[4],
            file_dependencies=json.loads(row[5]),
            file_dep_hash=row[6],
            file_write_dependencies=json.loads(row[7]),
            code_dependencies=json.loads(row[8]),
            run_duration_ns=row[9],
            hits=row[10],
            last_hit_at=row[11],
        )

    def get_fast(
        self, key: bytes
    ) -> tuple[str, bytes | None, list[str], list[str], bytes, int] | None:
        """Hot-path lookup: returns the minimum needed for a cache hit.

        Returns ``(serializer, file_dep_hash, file_read_deps, file_write_deps,
        key, run_duration_ns)`` or ``None`` if the key isn't present. Skips
        ``code_dependencies`` and avoids a JSON parse when dep lists are
        the literal empty-list string.
        """
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT serializer, file_dep_hash, file_dependencies, "
            "file_write_dependencies, run_duration_ns "
            "FROM entries WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        reads_json, writes_json = row[2], row[3]
        reads: list[str] = [] if reads_json == "[]" else json.loads(reads_json)
        writes: list[str] = [] if writes_json == "[]" else json.loads(writes_json)
        return (row[0], row[1], reads, writes, key, row[4])

    def get_payload(self, key: bytes) -> bytes | None:
        # Hot path: use str + builtin open to skip pathlib's __init__ chain.
        path = self._blob_path_str(key)
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def hit(self, key: bytes, *, eager: bool = True) -> None:
        """Mark a key as hit.

        With ``eager=True`` (default), updates the counter immediately. With
        ``eager=False`` the key is buffered; call :meth:`flush_hits` to commit.
        Saves a SQL UPDATE per hit on hot loops.

        When buffered, the buffer auto-flushes once it reaches
        ``_PENDING_HITS_FLUSH_AT`` so a long-running process can't accumulate
        unbounded keys in memory.
        """
        if not eager:
            self._pending_hits.append(key)
            if len(self._pending_hits) >= _PENDING_HITS_FLUSH_AT:
                self.flush_hits()
            return
        assert self._conn is not None
        self._conn.execute(
            "UPDATE entries SET hits=hits+1, last_hit_at=? WHERE key=?",
            (int(time.time()), key),
        )

    def flush_hits(self) -> None:
        """Commit any deferred hit updates."""
        if not self._pending_hits or self._conn is None:
            return
        # Group by key and bump counters in one round-trip per key.
        from collections import Counter

        counts = Counter(self._pending_hits)
        now = int(time.time())
        self._conn.executemany(
            "UPDATE entries SET hits=hits+?, last_hit_at=? WHERE key=?",
            [(n, now, k) for k, n in counts.items()],
        )
        self._pending_hits.clear()

    # ----- iteration / management

    def all_entries(self) -> list[Entry]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT key, function_name, created_at, size_bytes, serializer, "
            "file_dependencies, file_dep_hash, file_write_dependencies, "
            "code_dependencies, run_duration_ns, hits, last_hit_at "
            "FROM entries ORDER BY created_at DESC"
        ).fetchall()
        return [
            Entry(
                key=r[0],
                function_name=r[1],
                created_at=r[2],
                size_bytes=r[3],
                serializer=r[4],
                file_dependencies=json.loads(r[5]),
                file_dep_hash=r[6],
                file_write_dependencies=json.loads(r[7]),
                code_dependencies=json.loads(r[8]),
                run_duration_ns=r[9],
                hits=r[10],
                last_hit_at=r[11],
            )
            for r in rows
        ]

    def delete(self, key: bytes) -> bool:
        assert self._conn is not None
        cur = self._conn.execute("DELETE FROM entries WHERE key=?", (key,))
        with contextlib.suppress(FileNotFoundError):
            self._blob_path(key).unlink()
        return cur.rowcount > 0

    def delete_function(self, function_name: str) -> int:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT key FROM entries WHERE function_name=?", (function_name,)
        ).fetchall()
        for (key,) in rows:
            with contextlib.suppress(FileNotFoundError):
                self._blob_path(key).unlink()
        cur = self._conn.execute("DELETE FROM entries WHERE function_name=?", (function_name,))
        return int(cur.rowcount)

    def clear(self) -> int:
        assert self._conn is not None
        n = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        self._conn.execute("DELETE FROM entries")
        # Remove every blob.
        for sub in (self.cache_dir / "blobs").iterdir():
            for blob in sub.iterdir():
                with contextlib.suppress(FileNotFoundError):
                    blob.unlink()
        return int(n)

    def stats(self) -> dict[str, int]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0), COALESCE(SUM(hits), 0), "
            "COALESCE(SUM(run_duration_ns * hits), 0) FROM entries"
        ).fetchone()
        return {
            "entries": int(rows[0]),
            "total_bytes": int(rows[1]),
            "total_hits": int(rows[2]),
            "estimated_ns_saved": int(rows[3]),
        }
