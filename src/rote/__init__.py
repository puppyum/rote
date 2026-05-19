"""rote — automatic, dependency-aware memoization for Python.

A modern pure-Python reimplementation of IncPy (Guo & Engler, ISSTA 2011) without
an interpreter fork.
"""

from __future__ import annotations

from .config import Config, configure, get_config
from .session import auto, cache, clear, graph, invalidate, stats

__all__ = [
    "Config",
    "auto",
    "cache",
    "clear",
    "configure",
    "get_config",
    "graph",
    "invalidate",
    "stats",
]

__version__ = "0.1.0"
