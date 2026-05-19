"""Runtime configuration. Single source of truth for thresholds and paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


@dataclass
class Config:
    """Configuration for an rote session."""

    # Where to put the cache. Defaults to .rote in CWD.
    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get("ROTE_DIR", ".rote")))

    # Minimum measured wall-clock seconds for a call to be eligible for memoization.
    # Matches the paper §3.5 1-second threshold.
    min_duration_s: float = field(default_factory=lambda: _env_float("ROTE_MIN_DURATION_S", 1.0))

    # Maximum bytes for a single cached return value. Larger results are not cached
    # (logged as 'skipped: too large').
    max_value_bytes: int = 1 << 30  # 1 GiB

    # Whether to write a per-session telemetry log (Layer C instrumentation).
    telemetry: bool = True

    # Whether the automatic mode should monkey-patch the import system.
    install_import_hook: bool = True

    # Verbose logging to stderr.
    verbose: bool = False

    # If True, cache writes are skipped (useful for benchmarking pure tracer overhead).
    read_only: bool = False

    # If True (default), every blob write is fsync'd. Disable for benchmarks
    # or ephemeral CI caches where power-loss durability is irrelevant —
    # disabling saves ~500µs per write.
    fsync_writes: bool = True

    # If True (default), hit counters update in the foreground on every cache
    # hit. Setting to False defers updates to session shutdown; saves ~5µs
    # per hit at the cost of slightly stale telemetry mid-session.
    eager_hit_counters: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.cache_dir, Path):
            self.cache_dir = Path(self.cache_dir)


_global_config: Config = Config()


def configure(**kwargs: object) -> Config:
    """Update the global config in place. Returns the updated config."""
    for k, v in kwargs.items():
        if not hasattr(_global_config, k):
            raise AttributeError(f"Unknown config option: {k}")
        setattr(_global_config, k, v)
    _global_config.__post_init__()
    return _global_config


def get_config() -> Config:
    """Return the current global config."""
    return _global_config
