"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rote import configure, session


@pytest.fixture(autouse=True)
def _disable_perf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests assert against cache mechanics directly. The perf guard
    (paper §3.3.2) blacklists functions whose encode+write time dwarfs
    their body — entirely reasonable in production, but on slow filesystems
    (NTFS, network mounts, cold CI runners) it kicks in for the trivial
    test bodies and masks the cache behaviour we're trying to verify.
    Push the threshold past anything a real test will hit.
    """
    monkeypatch.setattr(session, "_PERF_GUARD_MIN_WRITE_NS", 10**18)
    session._PERF_BLACKLIST.clear()


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
    )
    session._reset_for_testing()
    yield cache_dir
    session._reset_for_testing()
