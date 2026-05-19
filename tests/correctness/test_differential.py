"""Differential testing: every corpus script must produce byte-identical
stdout under plain Python *and* under rote.run with any cache state.

Stale results are a P0 bug. This is the test that catches them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
ROTE_BIN = [sys.executable, "-m", "rote.cli"]
SCRIPTS = sorted(CORPUS.glob("c*.py"))


def _plain(script: Path, cwd: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=cwd,
    )
    return r.stdout


def _rote(script: Path, cwd: Path, cache_dir: Path) -> str:
    r = subprocess.run(
        [*ROTE_BIN, "--cache-dir", str(cache_dir), "run", str(script)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        cwd=cwd,
    )
    return r.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_cold_cache_matches_plain(script, tmp_path):
    """First run under rote == plain Python."""
    cache_dir = tmp_path / "cache"
    workdir = tmp_path / "work"
    workdir.mkdir()
    plain = _plain(script, workdir)
    cached = _rote(script, workdir, cache_dir)
    assert plain == cached, f"mismatch on {script.name}: plain={plain!r} cached={cached!r}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_warm_cache_matches_plain(script, tmp_path):
    """Second run (warm cache) == plain Python."""
    cache_dir = tmp_path / "cache"
    workdir = tmp_path / "work"
    workdir.mkdir()
    plain = _plain(script, workdir)
    _rote(script, workdir, cache_dir)  # populate cache
    # Reset state files (some scripts write fixtures)
    shutil.rmtree(workdir)
    workdir.mkdir()
    cached = _rote(script, workdir, cache_dir)
    assert plain == cached, f"mismatch on {script.name}: plain={plain!r} cached={cached!r}"
