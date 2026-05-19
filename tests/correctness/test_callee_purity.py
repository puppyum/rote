"""Tests that the @cache decorator now actually consults the impure-stdlib
list via static bytecode analysis + per-call sys.monitoring (Gap E from
the paper-fidelity audit).

Before the fix, only audit-hook events (network, exec, append-open) gated
the cache write. Calls to time.time(), random.random(), os.environ.get(),
etc., were silently cached as pure — a P0 correctness gap.
"""

from __future__ import annotations

# Module-level imports — typical pattern for research scripts. These bind
# in globals so LOAD_GLOBAL fires inside decorated functions.
import os
import random
import time as _time

import pytest

import rote


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


@rote.cache
def _stamped():
    return _time.time()


@rote.cache
def _rng():
    return random.random()


@rote.cache
def _env_dep():
    return os.environ.get("PATH", "")


@rote.cache
def _list_dir(d):
    return len(os.listdir(d))


def test_function_calling_time_time_is_not_cached():
    """time.time() is on the impure list — calls into it must skip the write."""
    _stamped()
    _stamped()
    stats = rote.stats()
    assert stats["impure_skips"] >= 1, f"expected impure_skip; got {stats}"


def test_function_calling_random_is_not_cached():
    _rng()
    _rng()
    stats = rote.stats()
    assert stats["impure_skips"] >= 1


def test_function_using_os_environ_is_not_cached():
    _env_dep()
    _env_dep()
    stats = rote.stats()
    # os.environ is in IMPURE_SYMBOLS; the static check catches the LOAD_ATTR.
    assert stats["impure_skips"] >= 1


def test_pure_math_function_is_still_cached():
    """Sanity: don't over-flag. Functions calling only math.* should cache."""
    import math

    @rote.cache
    def hypotenuse(a, b):
        return math.sqrt(a * a + b * b)

    hypotenuse(3, 4)
    hypotenuse(3, 4)
    stats = rote.stats()
    assert stats["hits"] >= 1, f"pure math call over-flagged: {stats}"


def test_function_calling_os_listdir_is_not_cached(tmp_path):
    (tmp_path / "a").write_text("")
    (tmp_path / "b").write_text("")
    _list_dir(str(tmp_path))
    _list_dir(str(tmp_path))
    stats = rote.stats()
    assert stats["impure_skips"] >= 1, f"os.listdir not flagged: {stats}"


def test_no_overhead_when_decorator_unused():
    """When no @cache function is in flight, the per-call monitor must be off."""
    import sys
    # Tool slot 5 should be free.
    assert sys.monitoring.get_tool(5) in (None, "rote-decorator")
