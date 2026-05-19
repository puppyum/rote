"""End-to-end: `rote run` on a slow script must be substantially faster
on the second invocation thanks to auto-mode wrapping.

This is THE headline-feature test. If it fails, the no-decorator promise is broken.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROTE = [sys.executable, "-m", "rote.cli"]
ENV = {**os.environ, "ROTE_MIN_DURATION_S": "0"}


SLOW_SCRIPT = """
def slow_pi(n):
    total = 0.0
    for k in range(n):
        total += ((-1) ** k) / (2 * k + 1)
    return 4.0 * total

def main():
    print(round(slow_pi(3_000_000), 8))

main()
"""


def _run(cache_dir, script, cwd):
    t0 = time.perf_counter()
    r = subprocess.run(
        ROTE + ["--cache-dir", str(cache_dir), "run", str(script)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=cwd,
        env=ENV,
    )
    return r.stdout, time.perf_counter() - t0


def test_auto_mode_speeds_up_repeat_run(tmp_path):
    cache_dir = tmp_path / "cache"
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = workdir / "slow.py"
    script.write_text(SLOW_SCRIPT)

    out_cold, t_cold = _run(cache_dir, script, workdir)
    out_warm, t_warm = _run(cache_dir, script, workdir)

    # Outputs must match exactly.
    assert out_cold == out_warm, f"output diverged: {out_cold!r} vs {out_warm!r}"
    # Second run must be substantially faster. We allow 60% to absorb noise
    # on loaded test runners; in practice on a quiet machine warm is <30%.
    assert t_warm < t_cold * 0.6, (
        f"expected warm to be <60% of cold; got cold={t_cold:.3f}s warm={t_warm:.3f}s"
    )


def test_auto_mode_invalidates_when_source_changes(tmp_path):
    """Edit the script; the cache must invalidate, output must update."""
    cache_dir = tmp_path / "cache"
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = workdir / "v.py"

    script.write_text("""
def f(n):
    return sum(range(n))

def main():
    print(f(1000))

main()
""")
    out1, _ = _run(cache_dir, script, workdir)
    assert out1.strip() == "499500"

    # Change literal — semantic edit, MUST invalidate.
    script.write_text("""
def f(n):
    return sum(range(n)) + 1

def main():
    print(f(1000))

main()
""")
    out2, _ = _run(cache_dir, script, workdir)
    assert out2.strip() == "499501", f"stale cache after edit: got {out2!r}"


def test_auto_mode_idempotent_with_comment_only_edit(tmp_path):
    """Cosmetic edit (comment) must NOT invalidate but MUST not break correctness."""
    cache_dir = tmp_path / "cache"
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = workdir / "c.py"
    script.write_text("def f(n):\n    return n * 2\n\ndef main():\n    print(f(7))\n\nmain()\n")
    out1, _ = _run(cache_dir, script, workdir)
    script.write_text("def f(n):\n    # touched\n    return n * 2\n\ndef main():\n    print(f(7))\n\nmain()\n")
    out2, _ = _run(cache_dir, script, workdir)
    assert out1 == out2 == "14\n"
