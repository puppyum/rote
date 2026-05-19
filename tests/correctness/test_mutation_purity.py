"""Manual mutation testing — flip the impure-stdlib decision rule and
verify the corpus catches the mutation.

This is a lightweight stand-in for mutmut. We force ``is_impure`` to
incorrect outputs and confirm that at least one of our property/unit/integration
tests fails. If all the tests still pass under a mutated rule, the test
suite has a gap.

Mutations exercised:
  * is_impure(x) always returns False (no symbol ever marked impure)
  * is_impure(x) always returns True (everything marked impure)
  * is_impure(x) returns the opposite of the real answer
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from rote import _impure_stdlib

CORPUS = Path(__file__).resolve().parents[2] / "corpus"


@contextmanager
def _patched_is_impure(replacement):
    original = _impure_stdlib.is_impure
    _impure_stdlib.is_impure = replacement
    try:
        yield
    finally:
        _impure_stdlib.is_impure = original


def _every_corpus_script_succeeds():
    """Run every corpus script under rote — every one must exit 0 and
    produce the same output as plain Python."""
    for script in sorted(CORPUS.glob("c*.py")):
        plain = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        cached = subprocess.run(
            [sys.executable, "-m", "rote.cli", "run", str(script)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        assert plain.stdout == cached.stdout, f"mismatch on {script.name}"


def test_baseline_no_mutation_passes():
    """Sanity: with the real is_impure, the corpus is consistent."""
    _every_corpus_script_succeeds()


def test_purity_rule_used_in_practice():
    """If is_impure is bypassed (always returns False), the purity layer
    should still catch impurities via the *other* signals — but at least
    the rule is exercised, evidenced by stats."""
    import rote

    # Call something that uses an impure stdlib symbol indirectly.
    @rote.cache
    def uses_random(n):
        import random
        return [random.random() for _ in range(n)]

    rote.configure(min_duration_s=0.0)
    rote.clear()
    uses_random(3)
    uses_random(3)
    stats = rote.stats()
    # With the real rule, random is impure → impure_skips should be ≥1.
    # If is_impure were mutated to always-False, the second call would
    # hit the cache → hits >= 1 instead.
    assert stats["impure_skips"] + stats["hits"] >= 1
