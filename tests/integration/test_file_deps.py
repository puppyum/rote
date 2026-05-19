"""File-dep tracking: a cached call that reads a file must invalidate
when the file changes between runs."""

from __future__ import annotations

import subprocess
import sys

import pytest

ROTE = [sys.executable, "-m", "rote.cli"]

SCRIPT = """
def read_and_sum(path):
    total = 0
    with open(path) as f:
        for line in f:
            total += int(line.strip())
    return total

def main(path):
    print(read_and_sum(path))

main("data.txt")
"""


def _run(cache_dir, script, cwd):
    r = subprocess.run(
        ROTE + ["--cache-dir", str(cache_dir), "run", str(script)],
        capture_output=True, text=True, check=True, timeout=30, cwd=cwd,
    )
    return r.stdout


def test_file_change_invalidates_cached_call(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    cache_dir = tmp_path / "cache"
    script = workdir / "r.py"
    script.write_text(SCRIPT)
    data = workdir / "data.txt"

    # First run
    data.write_text("1\n2\n3\n")
    out1 = _run(cache_dir, script, workdir)
    assert out1.strip() == "6"

    # Re-run with same data — must equal
    out2 = _run(cache_dir, script, workdir)
    assert out2.strip() == "6"

    # Change the data — cache must invalidate
    data.write_text("10\n20\n30\n")
    out3 = _run(cache_dir, script, workdir)
    assert out3.strip() == "60", f"stale cache after file change: got {out3!r}"
