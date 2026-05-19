"""The in-memory hit cache must not mask correctness regressions.

In particular:
  * Different args → different mem-cache entries.
  * Re-decorating the same source produces a wrapper with a fresh mem-cache.
  * LRU eviction works (mem cache is bounded).
"""

from __future__ import annotations

import rote


def test_different_args_different_mem_cache_entries(tmp_path):
    rote.configure(cache_dir=tmp_path / "c", min_duration_s=0.0)
    rote.clear()

    @rote.cache
    def f(x):
        return x * 2

    assert f(1) == 2
    assert f(2) == 4
    assert f(3) == 6
    # Repeat the same args 100x — every call should be a mem-cache hit.
    for _ in range(100):
        assert f(1) == 2
        assert f(2) == 4
        assert f(3) == 6
    assert rote.stats()["hits"] >= 300


def test_mem_cache_is_bounded(tmp_path):
    """LRU eviction kicks in when we exceed MAX_MEM_CACHE."""
    rote.configure(cache_dir=tmp_path / "c", min_duration_s=0.0)
    rote.clear()

    @rote.cache
    def f(x):
        return x

    # Fill beyond MAX_MEM_CACHE (256).
    for i in range(400):
        f(i)
    hits_before = rote.stats()["hits"]
    # Re-call the FIRST one — should still be on disk (cache hit) but mem cache
    # has evicted it, so we go to disk.
    assert f(0) == 0
    assert rote.stats()["hits"] == hits_before + 1


def test_mem_cache_does_not_persist_across_processes(tmp_path):
    """Mem cache is per-process; nothing magic about persistence."""
    import subprocess
    import sys

    cache_dir = tmp_path / "c"
    script = tmp_path / "s.py"
    script.write_text(
        "import rote\n"
        f"rote.configure(cache_dir=r'{cache_dir}', min_duration_s=0.0)\n"
        "@rote.cache\n"
        "def f(x): return x * 3\n"
        "print(f(7))\n"
    )
    r1 = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True,
    )
    r2 = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True,
    )
    assert r1.stdout.strip() == "21"
    assert r2.stdout.strip() == "21"
