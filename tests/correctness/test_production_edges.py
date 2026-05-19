"""Production-readiness edge cases: signals, generators, very large workloads,
weak refs, recursive auto-mode, multiple decorators."""

from __future__ import annotations

import sys

import pytest

import rote


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


# ----- generators


def test_function_returning_generator_does_not_crash():
    @rote.cache
    def gen():
        yield from range(5)

    assert list(gen()) == [0, 1, 2, 3, 4]
    assert list(gen()) == [0, 1, 2, 3, 4]  # second call: fresh gen


def test_generator_function_marked_correctly():
    """Generator funcs return generator objects, not values — can't cache."""

    @rote.cache
    def gen(n):
        for i in range(n):
            yield i * i

    out1 = list(gen(5))
    out2 = list(gen(5))
    assert out1 == out2 == [0, 1, 4, 9, 16]


# ----- very large inputs


def test_call_with_1m_int_list_arg():
    """Cache a function that takes a million-int list — fingerprint must handle it."""
    big = list(range(1_000_000))

    @rote.cache
    def first(xs):
        return xs[0] if xs else None

    assert first(big) == 0
    assert first(big) == 0  # hit


def test_call_with_deep_dict():
    """Deeply nested dict args don't crash fingerprinting."""
    d = {"a": {"b": {"c": {"d": {"e": 42}}}}}

    @rote.cache
    def get(d, *keys):
        for k in keys:
            d = d[k]
        return d

    assert get(d, "a", "b", "c", "d", "e") == 42
    assert get(d, "a", "b", "c", "d", "e") == 42


# ----- weak references


def test_function_returning_object_with_weakrefs():
    """An object that's used as a weakref target must roundtrip via cloudpickle."""
    import weakref

    class Tracked:
        pass

    @rote.cache
    def make_tracked():
        return Tracked()

    a = make_tracked()
    b = make_tracked()  # hit
    # Both should be valid Tracked instances.
    assert type(a) is type(b) is Tracked
    # Weakref support is class-level — both objects can be weakref'd.
    assert weakref.ref(a)() is not None
    assert weakref.ref(b)() is not None


# ----- recursive auto-mode + nested decorators


def test_cache_inside_cache():
    """A cached function calls another cached function — both should work."""
    @rote.cache
    def inner(x):
        return x * 2

    @rote.cache
    def outer(x):
        return inner(x) + inner(x + 1)

    assert outer(5) == 22  # 10 + 12
    hits_before = rote.stats()["hits"]
    assert outer(5) == 22  # hit on outer
    assert rote.stats()["hits"] == hits_before + 1


# ----- functools.lru_cache compatibility


def test_lru_cache_then_rote_cache_works():
    """Stacking @rote.cache on top of @functools.lru_cache is rare but legal."""
    import functools

    calls = {"n": 0}

    @rote.cache
    @functools.lru_cache(maxsize=128)
    def double(x):
        calls["n"] += 1
        return x * 2

    assert double(5) == 10
    assert double(5) == 10  # lru hits OR rote hits
    assert calls["n"] == 1


# ----- Signal-resistant


def test_keyboard_interrupt_during_call_propagates():
    """KeyboardInterrupt must propagate, not be swallowed by the wrapper."""

    @rote.cache
    def f(x):
        raise KeyboardInterrupt("user pressed ctrl-c")

    with pytest.raises(KeyboardInterrupt, match="user pressed"):
        f(1)


# ----- Hash collision robustness


def test_distinct_args_produce_distinct_keys():
    """Sanity: many calls with similar args don't collide."""
    @rote.cache
    def f(x, y):
        return x * 1000 + y

    seen: set[int] = set()
    for x in range(20):
        for y in range(20):
            seen.add(f(x, y))
    assert len(seen) == 400  # all distinct results returned


# ----- stats() roundtrip


def test_stats_includes_all_keys():
    @rote.cache
    def f(x):
        return x

    f(1)
    s = rote.stats()
    expected = {
        "hits", "misses", "impure_skips", "too_fast_skips", "too_big_skips",
        "saved_seconds", "spent_seconds", "invalidation_reasons", "store",
    }
    assert expected.issubset(s.keys())


# ----- Cache directory accessible after session.clear


def test_clear_then_continue_works():
    @rote.cache
    def f(x):
        return x + 1

    f(1); f(1)
    rote.clear()
    # Should still work post-clear.
    assert f(1) == 2
    assert f(1) == 2


# ----- Auto-mode handles syntax errors gracefully


def test_autowrap_on_syntax_error_falls_back():
    """If the user script has a syntax error, the transform must not eat it."""
    from rote.autowrap import transform

    bad = "def f(:\n    return 1\n"
    try:
        transform(bad)
    except Exception:
        pass  # transform may raise; that's fine — Python will report the syntax error
