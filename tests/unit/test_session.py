"""Layer 5 — public API tests."""

from __future__ import annotations

import rote


def test_cache_decorator_persists(tmp_path):
    rote.configure(cache_dir=tmp_path / "c1", min_duration_s=0.0)

    @rote.cache
    def compute(x):
        return x * 2

    assert compute(5) == 10
    assert compute(5) == 10  # hit
    assert compute(6) == 12  # miss
    stats = rote.stats()
    assert stats["hits"] >= 1
    assert stats["store"]["entries"] >= 2


def test_cache_decorator_min_duration_skip(tmp_path):
    rote.configure(cache_dir=tmp_path / "c2", min_duration_s=1.0)
    calls = {"n": 0}

    @rote.cache
    def fast(x):
        calls["n"] += 1
        return x

    fast(1)
    fast(1)
    # min_duration_s=1.0 means we won't cache anything that takes <1s
    assert calls["n"] == 2


def test_cache_decorator_invalidates_on_arg_change(tmp_path):
    rote.configure(cache_dir=tmp_path / "c3", min_duration_s=0.0)

    @rote.cache
    def f(x):
        return x + 1

    f(1)
    f(2)
    f(1)  # hit
    stats = rote.stats()
    assert stats["hits"] >= 1
    assert stats["store"]["entries"] >= 2


def test_invalidate_by_function(tmp_path):
    rote.configure(cache_dir=tmp_path / "c4", min_duration_s=0.0)

    @rote.cache
    def f(x):
        return x

    f(1)
    n = rote.invalidate(f)
    assert n >= 1


def test_clear_all(tmp_path):
    rote.configure(cache_dir=tmp_path / "c5", min_duration_s=0.0)

    @rote.cache
    def f(x):
        return x

    f(1)
    f(2)
    assert rote.clear() >= 2


def test_stats_reports_hits_and_misses(tmp_path):
    rote.configure(cache_dir=tmp_path / "c6", min_duration_s=0.0)

    @rote.cache
    def f(x):
        return x

    f(1)
    f(1)
    f(2)
    s = rote.stats()
    assert s["misses"] >= 2
    assert s["hits"] >= 1


def test_auto_context_manager_does_not_crash(tmp_path):
    rote.configure(cache_dir=tmp_path / "c7", min_duration_s=0.0)
    with rote.auto():
        def square(x):
            return x * x

        assert square(4) == 16


def test_mutation_skips_write(tmp_path):
    rote.configure(cache_dir=tmp_path / "c8", min_duration_s=0.0)

    @rote.cache
    def appender(lst):
        lst.append(99)
        return sum(lst)

    a = [1, 2, 3]
    appender(a)
    # The decorator should have detected the in-place mutation and refused
    # to cache. We can verify by checking that the impure_skips counter
    # incremented.
    s = rote.stats()
    assert s["impure_skips"] >= 1
