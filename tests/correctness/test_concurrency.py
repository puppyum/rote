"""16-process hammer test: many processes writing to the same store, no corruption."""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from rote.store import Store


def _worker(cache_dir: str, pid_seed: int, n: int) -> int:
    s = Store(Path(cache_dir))
    written = 0
    for i in range(n):
        key = (pid_seed * 1000 + i).to_bytes(32, "big")
        payload = f"p={os.getpid()} i={i}".encode()
        try:
            s.put(key=key, function_name="worker", serializer="msgpack", payload=payload)
            written += 1
        except Exception as e:
            print(f"worker {os.getpid()} put failed: {e}")
    s.close()
    return written


@pytest.mark.concurrency
@pytest.mark.parametrize("n_workers", [4, 16])
def test_concurrent_writes_no_corruption(n_workers, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # Initialize the schema first by opening once.
    Store(cache_dir).close()
    n_per_worker = 20
    with mp.get_context("spawn").Pool(n_workers) as pool:
        results = pool.starmap(
            _worker,
            [(str(cache_dir), i, n_per_worker) for i in range(n_workers)],
        )
    assert sum(results) == n_workers * n_per_worker
    # Verify integrity: every entry's payload is intact.
    s = Store(cache_dir)
    try:
        entries = s.all_entries()
        assert len(entries) == n_workers * n_per_worker, (
            f"expected {n_workers * n_per_worker} entries, got {len(entries)}"
        )
        for e in entries:
            payload = s.get_payload(e.key)
            assert payload is not None, f"missing payload for {e.key.hex()}"
            assert len(payload) > 0
    finally:
        s.close()
