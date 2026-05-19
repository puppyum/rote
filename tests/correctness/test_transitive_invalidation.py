"""When a cached caller delegates to an inner function, editing the inner
function MUST invalidate the caller's cache entry. If it doesn't, we ship
stale results when users edit anything below their top-level main().

This is the test that catches the auto-mode invalidation bug.

We force caching by setting min_duration_s=0 via env var so even fast
functions get cached.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROTE = [sys.executable, "-m", "rote.cli"]


def _run(cache_dir, script, cwd):
    env = {**os.environ, "ROTE_MIN_DURATION_S": "0"}
    r = subprocess.run(
        ROTE + ["--cache-dir", str(cache_dir), "run", str(script)],
        capture_output=True, text=True, check=True, timeout=30, cwd=cwd, env=env,
    )
    return r.stdout


def test_editing_inner_function_invalidates_caller(tmp_path):
    """The killer test: main() calls inner(); we edit inner; main MUST re-run."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    cache_dir = tmp_path / "cache"
    script = workdir / "s.py"

    script.write_text(
        "def inner(x):\n"
        "    return x * 10\n"
        "\n"
        "def main():\n"
        "    print(inner(5))\n"
        "\n"
        "main()\n"
    )
    out1 = _run(cache_dir, script, workdir)
    assert out1.strip() == "50", f"first run wrong: {out1!r}"

    # Edit inner — change the multiplier from 10 to 100.
    script.write_text(
        "def inner(x):\n"
        "    return x * 100\n"
        "\n"
        "def main():\n"
        "    print(inner(5))\n"
        "\n"
        "main()\n"
    )
    out2 = _run(cache_dir, script, workdir)
    assert out2.strip() == "500", (
        f"STALE RESULT after editing inner(): expected 500, got {out2!r}. "
        f"Cache invalidation is broken."
    )


def test_editing_deeply_nested_callee_invalidates_top(tmp_path):
    """main → a → b → c. Edit c. main MUST re-run."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    cache_dir = tmp_path / "cache"
    script = workdir / "s.py"

    script.write_text(
        "def c(x):\n    return x + 1\n"
        "def b(x):\n    return c(x) * 2\n"
        "def a(x):\n    return b(x) - 3\n"
        "def main():\n    print(a(10))\n"
        "main()\n"
    )
    out1 = _run(cache_dir, script, workdir)
    expected1 = str(((10 + 1) * 2) - 3)
    assert out1.strip() == expected1

    # Edit c: + 1 → + 100
    script.write_text(
        "def c(x):\n    return x + 100\n"
        "def b(x):\n    return c(x) * 2\n"
        "def a(x):\n    return b(x) - 3\n"
        "def main():\n    print(a(10))\n"
        "main()\n"
    )
    out2 = _run(cache_dir, script, workdir)
    expected2 = str(((10 + 100) * 2) - 3)
    assert out2.strip() == expected2, (
        f"STALE after editing deep callee: expected {expected2}, got {out2!r}"
    )
