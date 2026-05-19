"""End-to-end: running an example twice should be (a) faster and (b) identical."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EX_DIR = Path(__file__).resolve().parents[2] / "examples"
ROTE_BIN = [sys.executable, "-m", "rote.cli"]


@pytest.mark.parametrize("script", ["e1_compute_pi.py", "e4_numpy_pipeline.py"])
def test_cli_run_twice_is_identical(script, tmp_path):
    """Output of two consecutive runs must be byte-identical."""
    target = EX_DIR / script
    runs = []
    cache_dir = tmp_path / ".rote"
    for _ in range(2):
        r = subprocess.run(
            [*ROTE_BIN, "--cache-dir", str(cache_dir), "run", str(target)],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        runs.append(r.stdout)
    assert runs[0] == runs[1], f"stdout diverged: {runs[0]!r} vs {runs[1]!r}"


def test_cli_status_and_clear(tmp_path):
    cache_dir = tmp_path / ".rote"
    r = subprocess.run(
        [*ROTE_BIN, "--cache-dir", str(cache_dir), "run", str(EX_DIR / "e5_decorator_demo.py")],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert r.returncode == 0
    s = subprocess.run(
        [*ROTE_BIN, "--cache-dir", str(cache_dir), "status"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "hits" in s.stdout
    c = subprocess.run(
        [*ROTE_BIN, "--cache-dir", str(cache_dir), "clear"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "removed" in c.stdout


def test_decorator_speedup_on_repeat(tmp_path):
    """Decorator-based caching makes the second run faster.

    Uses a pure computation (not time.sleep, which is now correctly
    detected as impure and would not be cached).
    """
    import time

    import rote

    rote.configure(cache_dir=tmp_path / ".rote", min_duration_s=0.0)

    @rote.cache
    def slow(n):
        # Pure CPU work — no I/O, no time, no random.
        total = 0
        for k in range(n):
            total += (k * k) % 7919
        return total

    t0 = time.perf_counter()
    for i in range(10):
        slow(50_000 + i)
    cold = time.perf_counter() - t0

    t0 = time.perf_counter()
    for i in range(10):
        slow(50_000 + i)
    warm = time.perf_counter() - t0

    assert warm < cold * 0.5, f"warm {warm:.3f} not <50% of cold {cold:.3f}"
