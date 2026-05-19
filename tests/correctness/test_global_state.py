"""Paper §3.4: globally-reachable Python values that the function reads
must be part of the cache key. Editing a module-level constant must
invalidate any cached call that read it.
"""

from __future__ import annotations

import pytest

import rote

# Module-level state — referenced inside a cached function below.
THRESHOLD = 10
CONFIG = {"multiplier": 2, "offset": 0}


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    global THRESHOLD, CONFIG
    THRESHOLD = 10
    CONFIG = {"multiplier": 2, "offset": 0}
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


def test_module_level_int_change_invalidates():
    """Editing a module-level int that the function reads must invalidate."""

    @rote.cache
    def above(x):
        return x > THRESHOLD

    assert above(5) is False
    assert above(15) is True
    # Change the threshold — same args, but answer changes.
    global THRESHOLD
    THRESHOLD = 100
    assert above(5) is False
    assert above(15) is False, "Stale cache! THRESHOLD change not detected"


def test_module_level_dict_mutation_invalidates():
    """Mutating a module-level dict's contents must invalidate."""

    @rote.cache
    def transform(x):
        return x * CONFIG["multiplier"] + CONFIG["offset"]

    assert transform(5) == 10
    CONFIG["multiplier"] = 3
    assert transform(5) == 15, "Stale cache! CONFIG mutation not detected"


def test_unchanged_global_keeps_cache_hit():
    """Sanity: not over-invalidating. Same global → same cache hit."""

    @rote.cache
    def compute(x):
        return x + THRESHOLD

    compute(1)
    compute(1)
    compute(1)
    assert rote.stats()["hits"] >= 2


def test_pure_function_with_no_globals():
    """Functions that don't read any globals should still work."""
    @rote.cache
    def pure(x, y):
        return x * y + 1

    assert pure(3, 4) == 13
    assert pure(3, 4) == 13
    stats = rote.stats()
    assert stats["hits"] >= 1
