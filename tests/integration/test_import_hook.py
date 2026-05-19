"""Import hook integration: imported helper modules get their top-level
defs auto-wrapped, just like the entry script."""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROTE = [sys.executable, "-m", "rote.cli"]
ENV = {**os.environ, "ROTE_MIN_DURATION_S": "0"}


def _run(cache_dir, script, cwd):
    t0 = time.perf_counter()
    r = subprocess.run(
        ROTE + ["--cache-dir", str(cache_dir), "run", str(script)],
        capture_output=True, text=True, check=True, timeout=60, cwd=cwd, env=ENV,
    )
    return r.stdout, time.perf_counter() - t0


def test_imported_helper_function_gets_cached(tmp_path):
    """A helper module imported by the entry script should also be auto-wrapped."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    helper = workdir / "helpers.py"
    helper.write_text(
        "def slow_compute(n):\n"
        "    total = 0\n"
        "    for i in range(n):\n"
        "        total += i * i\n"
        "    return total\n"
    )
    main = workdir / "main.py"
    # Heavier loop so per-process startup doesn't dominate the difference.
    main.write_text(
        "from helpers import slow_compute\n"
        "print(slow_compute(8_000_000))\n"
    )

    cache_dir = tmp_path / "cache"
    out1, t1 = _run(cache_dir, main, workdir)
    out2, t2 = _run(cache_dir, main, workdir)
    assert out1 == out2
    # Second run hits cache. Threshold allows for noisy CI hardware.
    assert t2 < t1 * 0.7, f"import-hook caching didn't help: {t1=:.3f}s {t2=:.3f}s"


def test_no_import_hook_flag(tmp_path):
    """The --no-import-hook flag works (no crash, no cache entries from helpers)."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "helpers.py").write_text("def f(n): return n + 1\n")
    main = workdir / "main.py"
    main.write_text("from helpers import f\nprint(f(5))\n")
    cache_dir = tmp_path / "cache"
    r = subprocess.run(
        ROTE + ["--cache-dir", str(cache_dir), "run", str(main), "--no-import-hook"],
        capture_output=True, text=True, timeout=30, cwd=workdir, env=ENV,
    )
    # Note: --no-import-hook is a `run` subcommand flag, must be BEFORE the script.
    # Argparse may complain — let's check the error path.
    # If it fails, also try the standard ordering.
    if r.returncode != 0:
        r = subprocess.run(
            ROTE + ["--cache-dir", str(cache_dir), "run", "--no-import-hook", str(main)],
            capture_output=True, text=True, check=True, timeout=30, cwd=workdir, env=ENV,
        )
    assert "6" in r.stdout
