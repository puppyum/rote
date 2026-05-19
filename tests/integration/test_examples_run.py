"""Integration: every example script runs without error under rote auto()."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

import rote

EXAMPLES = sorted((Path(__file__).resolve().parents[2] / "examples").glob("*.py"))


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.stem)
def test_example_runs_under_auto(script, tmp_path, monkeypatch):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(script.parent))
    # Save argv so a script reading sys.argv doesn't choke.
    monkeypatch.setattr(sys, "argv", [str(script)])
    with rote.auto():
        runpy.run_path(str(script), run_name="__main__")


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.stem)
def test_example_runs_plain(script, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(script.parent))
    monkeypatch.setattr(sys, "argv", [str(script)])
    runpy.run_path(str(script), run_name="__main__")
