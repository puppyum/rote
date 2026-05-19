"""Layer 4 — Purity / mutation detection.

A call is "pure-enough" to memoize iff *all* of the following are true:

1. No audit-hook event during the call signals network / exec / file-append /
   unclosed-write.
2. No transitively-called function is on the curated impure stdlib list.
3. No input argument's content fingerprint changed between entry and exit.

This module exposes a :class:`PurityTracker` that wires into a :class:`Tracer`.
The tracker maintains a per-call frame in :attr:`stack`, recording entry
fingerprints, observed audit events, and a final "verdict".
"""

from __future__ import annotations

import os
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from typing import Any

from . import _impure_stdlib
from .trace import EventKind, TraceEvent, Tracer


@dataclass
class CallFrame:
    """State for one in-flight call."""

    code: CodeType
    qualname: str
    module: str | None
    started_ns: int
    arg_fingerprints: dict[str, bytes] = field(default_factory=dict)
    file_reads: list[str] = field(default_factory=list)
    file_writes_open: dict[str, str] = field(default_factory=dict)  # path -> mode
    file_writes_closed: list[str] = field(default_factory=list)
    impure_reasons: list[str] = field(default_factory=list)
    duration_ns: int = 0


@dataclass
class Verdict:
    """Final pure/impure ruling for a completed call."""

    pure: bool
    duration_ns: int
    reasons: list[str]
    file_read_deps: list[str]
    file_write_deps: list[str]


class PurityTracker:
    """Decides whether each completed call is safe to memoize.

    Wires into a :class:`Tracer` via the listener API. Maintains a stack
    parallel to the Python frame stack. Emits verdicts via the
    :meth:`on_verdict` callback when frames complete.
    """

    # Bounded by VERDICTS_CAP. In production the wrapper does not read this
    # dict, but ``auto()`` mode and diagnostic tests do — and a long-running
    # session can call millions of distinct Python functions. Cap the dict
    # so we keep the most-recent N verdicts (FIFO) instead of leaking one
    # entry per frame entered.
    VERDICTS_CAP = 1024

    def __init__(self, tracer: Tracer) -> None:
        self.tracer: Tracer = tracer
        self.stack: list[CallFrame] = []
        # OrderedDict so insertion order tracks recency; evict the oldest
        # when over cap. Keyed by id(code) of the most recently completed
        # frame.
        self.verdicts: OrderedDict[int, Verdict] = OrderedDict()
        self._tracer_handle = self._on_event
        tracer.add_listener(self._tracer_handle)

    def close(self) -> None:
        self.tracer.remove_listener(self._tracer_handle)

    # ----- listener

    def _on_event(self, ev: TraceEvent) -> None:
        if ev.kind == EventKind.CALL:
            self._on_call(ev)
        elif ev.kind in (EventKind.RETURN, EventKind.UNWIND):
            self._on_return(ev)
        elif ev.kind == EventKind.FILE_OPEN:
            self._on_file_open(ev)
        elif ev.kind == EventKind.NETWORK:
            self._mark_all("network I/O")
        elif ev.kind == EventKind.EXEC:
            self._mark_all("exec/eval/compile")
        # RAISE is informational only — the frame may catch the exception.

    # ----- call lifecycle

    def _on_call(self, ev: TraceEvent) -> None:
        if ev.code is None:  # defensive
            return
        frame = CallFrame(
            code=ev.code,
            qualname=ev.func_qualname or "?",
            module=ev.func_module,
            started_ns=ev.t_ns,
        )
        # Check impure-stdlib list at call entry. The qualname here is the
        # callee, e.g. "Random.random" — we need module+qualname.
        dotted = f"{ev.func_module}.{ev.func_qualname}" if ev.func_module else (ev.func_qualname or "")
        if _impure_stdlib.is_impure(dotted):
            frame.impure_reasons.append(f"calls impure stdlib: {dotted}")
            # Propagate to ancestors too — they called something impure.
            for f in self.stack:
                f.impure_reasons.append(f"transitively calls impure: {dotted}")
        self.stack.append(frame)

    def _on_return(self, ev: TraceEvent) -> None:
        if not self.stack:
            return
        frame = self.stack.pop()
        frame.duration_ns = ev.t_ns - frame.started_ns
        # Any open writes that weren't closed by the function are impure.
        for path, mode in frame.file_writes_open.items():
            if "a" in mode:
                frame.impure_reasons.append(f"appends to {path} (mode {mode})")
            else:
                frame.impure_reasons.append(f"left {path} open at exit (mode {mode})")
        verdict = Verdict(
            pure=not frame.impure_reasons,
            duration_ns=frame.duration_ns,
            reasons=list(frame.impure_reasons),
            file_read_deps=sorted(set(frame.file_reads)),
            file_write_deps=sorted(frame.file_writes_closed),
        )
        self.verdicts[id(frame.code)] = verdict
        if len(self.verdicts) > self.VERDICTS_CAP:
            self.verdicts.popitem(last=False)

    # ----- audit-hook plumbing

    def _on_file_open(self, ev: TraceEvent) -> None:
        path = ev.payload.get("path")
        if not path:
            return
        mode = ev.payload.get("mode") or "r"
        if not self.stack:
            return
        top = self.stack[-1]
        if any(c in mode for c in ("w", "x", "a")):
            top.file_writes_open[path] = mode
            # Append is always impure for the current function and ancestors.
            if "a" in mode:
                self._mark_all(f"append-mode open of {path}")
        else:
            top.file_reads.append(path)

    def _mark_all(self, reason: str) -> None:
        for f in self.stack:
            f.impure_reasons.append(reason)

# ----------------------------------------------------- Public convenience


# Types we treat as deeply immutable — no need to re-fingerprint at call exit.
_IMMUTABLE_TYPES: tuple[type, ...] = (int, float, complex, str, bytes, bool, type(None), frozenset)


def _is_definitely_immutable(value: Any) -> bool:
    if isinstance(value, _IMMUTABLE_TYPES):
        return True
    if isinstance(value, tuple):
        # Tuples are immutable containers but their elements may not be.
        return all(_is_definitely_immutable(v) for v in value)
    return False


def mutable_value_names(values: dict[str, Any]) -> set[str]:
    """Return names whose bound values could possibly mutate."""
    return {k for k, v in values.items() if not _is_definitely_immutable(v)}


def args_changed(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    """Return the names of args whose fingerprint changed between two snapshots."""
    changed: list[str] = []
    for k, v in before.items():
        if after.get(k) != v:
            changed.append(k)
    return changed


_ContentHashKey = tuple[str, int, int, int, int, int]
_CONTENT_HASH_CACHE: dict[_ContentHashKey, bytes] = {}
_CONTENT_HASH_CACHE_LIMIT = 1024


def _file_content_digest(path: Path) -> bytes:
    try:
        import blake3 as _blake3_mod  # type: ignore[import-untyped]

        h: Any = _blake3_mod.blake3()
    except ImportError:  # pragma: no cover
        import hashlib

        h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return bytes(h.digest())


def _content_hash_cache_key(path: Path, st: os.stat_result) -> _ContentHashKey | None:
    if os.name != "posix":
        return None
    return (
        str(path),
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_ctime_ns),
    )


def _cached_file_content_digest(path: Path, st: os.stat_result) -> bytes:
    key = _content_hash_cache_key(path, st)
    if key is None:
        return _file_content_digest(path)
    cached = _CONTENT_HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = _file_content_digest(path)
    _CONTENT_HASH_CACHE[key] = digest
    if len(_CONTENT_HASH_CACHE) > _CONTENT_HASH_CACHE_LIMIT:
        _CONTENT_HASH_CACHE.pop(next(iter(_CONTENT_HASH_CACHE)))
    return digest


def file_dep_hash(paths: list[str]) -> bytes:
    """Hash a sorted file-dep list using file contents, not metadata proxies."""
    from .serialize import _hash  # type: ignore[attr-defined]

    parts: list[bytes] = []
    for p in sorted(set(paths)):
        try:
            path = Path(p)
            st = path.stat()
            if not stat.S_ISREG(st.st_mode):
                marker = time.perf_counter_ns()
                parts.append(f"{p}:nonregular:{st.st_mode}:{marker}".encode())
                continue
            parts.append(f"{p}:{st.st_size}:".encode() + _cached_file_content_digest(path, st))
        except OSError:
            parts.append(f"{p}:missing".encode())
    return _hash(b"\0".join(parts))


def now_ns() -> int:
    return time.perf_counter_ns()
