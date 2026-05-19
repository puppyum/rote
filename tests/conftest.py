"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rote import configure, session


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path) -> Path:
    """Each test gets its own .rote cache directory + a freshly-defaulted Config."""
    cache_dir = tmp_path / ".rote"
    cache_dir.mkdir()
    os.environ["ROTE_DIR"] = str(cache_dir)
    configure(
        cache_dir=cache_dir,
        telemetry=False,
        min_duration_s=0.0,
        max_value_bytes=1 << 30,
        read_only=False,
        fsync_writes=True,
        eager_hit_counters=True,
        verbose=False,
        install_import_hook=True,
    )
    session._reset_for_testing()
    yield cache_dir
    session._reset_for_testing()
