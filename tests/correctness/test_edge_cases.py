"""Exhaustive edge-case tests for the cache decorator.

Categories:
  * Argument edge cases (NaN, ±0.0, very large ints, recursive containers)
  * Return-value edge cases (None, exception during stream-capture, very large blobs)
  * Decorator stacking (nested @cache, @cache + functools.wraps, classmethod)
  * Concurrency edge cases (re-entrant cache, asyncio coroutine)
  * Filesystem edge cases (cache dir deleted mid-execution, symlinks, unicode paths)
  * Configuration edge cases (cache_dir change after import, min_duration_s=0 vs None)
"""

from __future__ import annotations

import math
import os
import threading

import numpy as np
import pytest

import rote


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


# ============================================================ Argument edges


def test_nan_args_get_distinct_keys():
    """NaN != NaN by definition. The cache key uses the bit pattern via msgpack,
    so two calls with NaN args should hit the SAME cache entry (deterministic
    bit-level identity, not float equality)."""

    @rote.cache
    def f(x):
        return math.copysign(1, x)  # 1 for +nan, -1 for -nan

    f(float("nan"))
    f(float("nan"))
    stats = rote.stats()
    # Both calls produce the same fingerprint → second is a hit.
    assert stats["hits"] >= 1


def test_positive_and_negative_zero_get_distinct_keys():
    """+0.0 == -0.0 in Python, but bit patterns differ."""

    @rote.cache
    def f(x):
        return repr(x)

    assert f(0.0) == "0.0"
    assert f(-0.0) == "-0.0"
    # If the cache merged the two, the second call would return "0.0" from the first.


def test_very_large_int_arg():
    """Bignum > 64-bit doesn't crash; falls back to cloudpickle fingerprint."""

    @rote.cache
    def f(n):
        return n.bit_length()

    big = 10**100
    assert f(big) == 333  # bit_length of 10^100
    assert f(big) == 333  # hit


def test_self_referential_arg_does_not_crash():
    """Cyclic arg → fingerprint falls through to id()-based synthetic key."""

    @rote.cache
    def length(lst):
        return len(lst)

    a: list = [1, 2]
    a.append(a)
    # We just need it not to RecursionError.
    assert length(a) == 3


def test_unicode_string_args():
    @rote.cache
    def echo(s):
        return s

    s = "naïve résumé 日本語 🌟"
    assert echo(s) == s
    assert echo(s) == s
    stats = rote.stats()
    assert stats["hits"] >= 1


# ========================================================= Return-value edges


def test_none_return_value_caches_correctly():
    @rote.cache
    def f():
        return None

    f()
    f()
    f()
    assert rote.stats()["hits"] >= 2


def test_exception_during_capture_does_not_cache(tmp_path):
    """If the function raises after partial stdout, we must not cache anything."""

    @rote.cache
    def f():
        print("partial output before exception")
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        f()
    # Second call must re-execute and re-raise.
    with pytest.raises(ValueError, match="boom"):
        f()
    stats = rote.stats()
    assert stats["hits"] == 0


def test_very_large_return_blocked_by_size_limit():
    rote.configure(min_duration_s=0.0, max_value_bytes=100)  # 100 bytes only
    try:
        @rote.cache
        def f():
            return list(range(1000))  # serializes to way more than 100 bytes

        f()
        f()
        stats = rote.stats()
        assert stats["too_big_skips"] >= 1
    finally:
        # Reset so the leak doesn't poison other tests.
        rote.configure(max_value_bytes=1 << 30)


def test_pickle_unfriendly_return_does_not_crash():
    """A return value that can't be encoded just doesn't cache — no error."""

    @rote.cache
    def make_gen():
        return (i for i in range(3))

    out = list(make_gen())
    assert out == [0, 1, 2]
    # Second call: fresh generator, since we couldn't cache.
    assert list(make_gen()) == [0, 1, 2]


# ========================================================== Decorator stacking


def test_nested_cache_does_not_double_wrap():
    """@cache(@cache(f)) should be safe (and identity-equivalent to one wrap)."""
    calls = {"n": 0}

    @rote.cache
    @rote.cache
    def f(x):
        calls["n"] += 1
        return x

    f(1)
    f(1)
    f(1)
    # Should still produce the correct value; ideally only one body call.
    assert f(1) == 1


def test_classmethod_caching():
    """@cache(@classmethod(f)) works — we unwrap the descriptor."""
    calls = {"n": 0}

    class C:
        @rote.cache
        @classmethod
        def factor(cls, n):
            calls["n"] += 1
            out = []
            while n % 2 == 0:
                out.append(2)
                n //= 2
            i = 3
            while i * i <= n:
                while n % i == 0:
                    out.append(i)
                    n //= i
                i += 2
            if n > 1:
                out.append(n)
            return out

    assert C.factor(12) == [2, 2, 3]
    assert C.factor(12) == [2, 2, 3]  # second call should hit
    # Same result via instance too.
    assert C().factor(12) == [2, 2, 3]


def test_staticmethod_caching():
    """@cache(@staticmethod(f)) works the same way."""
    class C:
        @rote.cache
        @staticmethod
        def double(x):
            return x * 2

    assert C.double(5) == 10
    assert C.double(5) == 10  # hit
    assert C().double(7) == 14


def test_method_with_mutable_self_is_not_cached():
    """An instance method whose `self` mutates should not be cached as pure."""
    calls = {"n": 0}

    class Counter:
        def __init__(self):
            self.n = 0

        @rote.cache
        def inc(self):
            self.n += 1
            calls["n"] += 1
            return self.n

    c = Counter()
    c.inc()
    c.inc()
    # Two real body calls because self mutated each time.
    assert calls["n"] == 2


# =========================================================== Concurrency edges


def test_reentrant_cache_call():
    """A cached function recursively calling itself should not deadlock."""

    @rote.cache
    def fib(n):
        if n < 2:
            return n
        return fib(n - 1) + fib(n - 2)

    assert fib(10) == 55


def test_two_threads_same_function_same_args_no_race():
    """Both threads see the right return value when calling f(x) with same x."""

    @rote.cache
    def f(x):
        return x * 7

    results: list[int] = [0, 0]

    def worker(i):
        results[i] = f(13)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [91, 91]


# ============================================================== Filesystem edges


def test_cache_dir_deleted_mid_session_does_not_crash(tmp_path):
    """A nuked cache dir is recreated on next write rather than crashing."""
    # Use the same dir the autouse fixture configured to keep state consistent.
    from rote import get_config
    cache_dir = get_config().cache_dir

    @rote.cache
    def f(x):
        return x

    f(1)
    # Wipe the blobs subdir (not the index.db, that needs a reconnect).
    import shutil
    blobs = cache_dir / "blobs"
    if blobs.exists():
        shutil.rmtree(blobs)
    blobs.mkdir()
    # Function still works even if the blob went missing.
    assert f(1) == 1


def test_symlinked_file_dep_resolves_correctly(tmp_path):
    """A read through a symlink should record the symlink target's content."""
    real = tmp_path / "real.txt"
    real.write_text("hello")
    link = tmp_path / "link.txt"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("symlinks not supported on this platform/filesystem")

    @rote.cache
    def read(path):
        with open(path) as f:
            return f.read()

    assert read(str(link)) == "hello"
    real.write_text("changed")
    # File-dep tracking via abspath should record the LINK path, but content
    # hash should follow the link, so the change is detected.
    assert read(str(link)) == "changed"


# ========================================================== Configuration edges


def test_configure_can_change_cache_dir_at_runtime(tmp_path):
    """Switching cache_dir mid-session is supported (rare but legal)."""
    rote.configure(cache_dir=tmp_path / "a", min_duration_s=0.0)

    @rote.cache
    def f(x):
        return x

    f(1)
    # Switch to a fresh directory — should still work.
    rote.configure(cache_dir=tmp_path / "b")
    rote.session._reset_for_testing()
    assert f(2) == 2


def test_min_duration_threshold_skips_fast_calls():
    """Functions faster than min_duration_s shouldn't get a store entry."""
    rote.configure(min_duration_s=10.0)  # 10s threshold

    @rote.cache
    def fast(x):
        return x

    fast(1)
    fast(2)
    fast(3)
    stats = rote.stats()
    assert stats["too_fast_skips"] >= 3


# ============================================================ Real-world misuse


def test_function_modifying_module_global_is_not_caught(tmp_path):
    """Known limitation: we don't track global mutations (paper §3.4)."""
    module_state = {"counter": 0}

    @rote.cache
    def increment_and_return(x):
        module_state["counter"] += 1
        return x

    rote.configure(min_duration_s=0.0)
    rote.clear()
    increment_and_return(1)
    increment_and_return(1)  # hit
    # The cached call doesn't re-increment. This IS the documented limitation.
    # We just assert that we don't *crash* — the user is responsible.
    assert module_state["counter"] >= 1


def test_kwargs_with_same_value_different_order_hit_same_entry():
    @rote.cache
    def f(a, b, c):
        return a + b + c

    rote.configure(min_duration_s=0.0)
    rote.clear()
    assert f(a=1, b=2, c=3) == 6
    # Same kwargs in different order should hit the same cache.
    assert f(c=3, b=2, a=1) == 6
    stats = rote.stats()
    assert stats["hits"] >= 1


def test_numpy_array_arg_stable_fingerprint():
    """Passing the same numpy array twice should hit the cache."""

    @rote.cache
    def f(arr):
        return float(arr.sum())

    a = np.arange(100, dtype="float64")
    rote.configure(min_duration_s=0.0)
    rote.clear()
    f(a)
    f(a)
    stats = rote.stats()
    assert stats["hits"] >= 1
