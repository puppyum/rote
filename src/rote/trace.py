"""Layer 1 — Tracing.

Uses ``sys.monitoring`` (PEP 669) to observe function start/return/raise and
``sys.addaudithook`` (PEP 578) to observe file I/O, network, exec/eval, imports.

The tracer is purely an event source. It never decides whether to cache; it
emits structured :class:`TraceEvent` records that downstream layers consume.

Design notes
------------
* We register under a single ``TOOL_ID = 4`` (PROFILER slot). The constants are
  resolved at startup, never hard-coded, so future CPython renumbering is safe.
* Audit-hook callbacks classify events conservatively. When in doubt, mark
  impure — the purity layer makes the final call.
* Events are pushed to an in-memory deque (the "ring buffer"). When the buffer
  exceeds ``max_buffer``, oldest entries spill to a JSONL file inside the
  cache directory.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import CodeType
from typing import Any

# ----------------------------------------------------------------------- Event

TOOL_ID = 4  # PROFILER slot in sys.monitoring


class EventKind(StrEnum):
    """Coarse category of a TraceEvent. Stored as string for easy logging."""

    CALL = "call"  # Python function entry
    RETURN = "return"  # Python function exit (normal)
    UNWIND = "unwind"  # Python function exit (unhandled exception)
    RAISE = "raise"  # exception raised (may be caught, frame stays alive)
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"  # any non-self-contained write (incl. append)
    FILE_OPEN = "file_open"  # raw open event, mode resolved separately
    NETWORK = "network"
    EXEC = "exec"
    IMPORT = "import"


@dataclass(slots=True)
class TraceEvent:
    """A single observed event in execution order."""

    kind: EventKind
    t_ns: int
    # For CALL/RETURN/RAISE: code object (we keep a weak-ish ref via id).
    code: CodeType | None = None
    func_qualname: str | None = None
    func_module: str | None = None
    # For RETURN: a placeholder for the returned value (None unless captured).
    # Returned values are captured by Layer 4 directly via frame inspection;
    # we just note that a return happened.
    return_value: Any = None
    # For audit-hook events: free-form payload (filename, mode, host, etc.).
    payload: dict[str, Any] = field(default_factory=dict)
    # Frame depth at event time (CALL increments before push, RETURN decrements after).
    depth: int = 0


# ----------------------------------------------------------------- Audit hook

# These event names are what we care about. PEP 578 documents the full list.
_AUDIT_OPEN_EVENTS = frozenset({"open", "builtins.open"})
_AUDIT_EXEC_EVENTS = frozenset({"exec", "compile"})
_AUDIT_NETWORK_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "socket.send",
        "socket.recv",
        "socket.recvfrom",
        "urllib.Request",
        "http.client.connect",
        "ftplib.connect",
        "smtplib.connect",
    }
)
_AUDIT_IMPORT_EVENTS = frozenset({"import"})


def _classify_audit(event: str) -> EventKind | None:
    """Map a Python audit event name to a coarse EventKind."""
    if event in _AUDIT_OPEN_EVENTS:
        return EventKind.FILE_OPEN
    if event in _AUDIT_EXEC_EVENTS:
        return EventKind.EXEC
    if event in _AUDIT_NETWORK_EVENTS or event.startswith(("socket.", "urllib.", "http.client.")):
        return EventKind.NETWORK
    if event in _AUDIT_IMPORT_EVENTS:
        return EventKind.IMPORT
    return None


# ------------------------------------------------------------------ Tracer

# Module-level guard. Re-entering tracer callbacks would deadlock or recurse.
_local = threading.local()


def _entered() -> bool:
    return bool(getattr(_local, "in_tracer", False))


class Tracer:
    """Wraps ``sys.monitoring`` + audit-hook plumbing.

    The tracer is *not* a context manager itself — that role belongs to
    :func:`session.auto`. The tracer just exposes ``start()`` / ``stop()``
    and a ``buffer`` of :class:`TraceEvent`.
    """

    def __init__(self, max_buffer: int = 1 << 16, spill_path: Path | None = None) -> None:
        self.max_buffer: int = max_buffer
        self.spill_path: Path | None = spill_path
        self.buffer: deque[TraceEvent] = deque()
        self._depth: int = 0
        self._active: bool = False
        self._spilled: int = 0
        # Listeners called synchronously for every event. Used by the session
        # layer to wire purity tracking without polling the buffer.
        self._listeners: list[Callable[[TraceEvent], None]] = []

    # ----- listener interface

    def add_listener(self, listener: Callable[[TraceEvent], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[TraceEvent], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def clear_listeners(self) -> None:
        self._listeners.clear()

    # ----- lifecycle

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._depth = 0
        self.buffer.clear()
        self._install_monitoring()
        self._install_audit()

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._uninstall_monitoring()
        # Audit hooks cannot be uninstalled by design (PEP 578) — they are
        # process-global. We instead flip ``_active`` and the callback is a
        # no-op when inactive.

    # ----- monitoring

    def _install_monitoring(self) -> None:
        mon = sys.monitoring
        # If the tool id is occupied by someone else, fail loudly rather than
        # silently overwriting.
        existing = mon.get_tool(TOOL_ID)
        if existing not in (None, "rote"):
            raise RuntimeError(f"sys.monitoring tool id {TOOL_ID} is in use by {existing!r}")
        mon.use_tool_id(TOOL_ID, "rote")

        events = mon.events
        # PY_START enters a Python frame. PY_RETURN exits normally. PY_UNWIND
        # exits via an uncaught exception (frame is gone). PY_RESUME/PY_YIELD
        # are generator/coroutine bookkeeping. RAISE fires when an exception
        # is raised but does not necessarily mean the frame exits — it may be
        # caught upstream. We classify RAISE as informational only; pops
        # happen on RETURN, YIELD, or UNWIND.
        mask = (
            events.PY_START
            | events.PY_RETURN
            | events.PY_RESUME
            | events.PY_YIELD
            | events.PY_UNWIND
            | events.RAISE
        )
        mon.set_events(TOOL_ID, mask)
        mon.register_callback(TOOL_ID, events.PY_START, self._on_py_start)
        mon.register_callback(TOOL_ID, events.PY_RESUME, self._on_py_start)
        mon.register_callback(TOOL_ID, events.PY_RETURN, self._on_py_return)
        mon.register_callback(TOOL_ID, events.PY_YIELD, self._on_py_return)
        mon.register_callback(TOOL_ID, events.PY_UNWIND, self._on_py_unwind)
        mon.register_callback(TOOL_ID, events.RAISE, self._on_py_raise)

    def _uninstall_monitoring(self) -> None:
        mon = sys.monitoring
        try:
            for ev in (
                mon.events.PY_START,
                mon.events.PY_RESUME,
                mon.events.PY_RETURN,
                mon.events.PY_YIELD,
                mon.events.PY_UNWIND,
                mon.events.RAISE,
            ):
                mon.register_callback(TOOL_ID, ev, None)
            mon.set_events(TOOL_ID, 0)
            mon.free_tool_id(TOOL_ID)
        except (RuntimeError, ValueError):
            pass

    # ----- audit hook

    def _install_audit(self) -> None:
        # PEP 578: audit hooks can only be added, never removed. We add ours
        # once per process and gate it on ``self._active``.
        if not getattr(self, "_audit_installed", False):
            sys.addaudithook(self._on_audit)
            self._audit_installed = True

    # ----- callback bodies

    def _on_py_start(self, code: CodeType, instruction_offset: int) -> object:
        if not self._active or _entered():
            return sys.monitoring.DISABLE if not self._active else None
        _local.in_tracer = True
        try:
            self._depth += 1
            self._push(
                TraceEvent(
                    kind=EventKind.CALL,
                    t_ns=time.perf_counter_ns(),
                    code=code,
                    func_qualname=code.co_qualname,
                    func_module=_safe_module(code),
                    depth=self._depth,
                )
            )
        finally:
            _local.in_tracer = False
        return None

    def _on_py_return(self, code: CodeType, instruction_offset: int, retval: Any) -> object:
        if not self._active or _entered():
            return None
        _local.in_tracer = True
        try:
            ev = TraceEvent(
                kind=EventKind.RETURN,
                t_ns=time.perf_counter_ns(),
                code=code,
                func_qualname=code.co_qualname,
                func_module=_safe_module(code),
                return_value=retval,
                depth=self._depth,
            )
            self._push(ev)
            self._depth = max(0, self._depth - 1)
        finally:
            _local.in_tracer = False
        return None

    def _on_py_raise(self, code: CodeType, instruction_offset: int, exception: BaseException) -> object:
        # RAISE is informational: an exception was raised inside this frame,
        # but the frame may catch it and continue. Do NOT decrement depth.
        if not self._active or _entered():
            return None
        _local.in_tracer = True
        try:
            self._push(
                TraceEvent(
                    kind=EventKind.RAISE,
                    t_ns=time.perf_counter_ns(),
                    code=code,
                    func_qualname=code.co_qualname,
                    func_module=_safe_module(code),
                    payload={"exception_type": type(exception).__name__},
                    depth=self._depth,
                )
            )
        finally:
            _local.in_tracer = False
        return None

    def _on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> object:
        # PY_UNWIND: the frame is exiting via an uncaught exception. This is
        # the true "frame gone" signal for the exception path.
        if not self._active or _entered():
            return None
        _local.in_tracer = True
        try:
            self._push(
                TraceEvent(
                    kind=EventKind.UNWIND,
                    t_ns=time.perf_counter_ns(),
                    code=code,
                    func_qualname=code.co_qualname,
                    func_module=_safe_module(code),
                    payload={"exception_type": type(exception).__name__},
                    depth=self._depth,
                )
            )
            self._depth = max(0, self._depth - 1)
        finally:
            _local.in_tracer = False
        return None

    def _on_audit(self, event: str, args: tuple[Any, ...]) -> None:
        # Audit hook MUST NOT raise. Any exception here propagates as a
        # SystemError in CPython.
        if not self._active or _entered():
            return
        kind = _classify_audit(event)
        if kind is None:
            return
        _local.in_tracer = True
        try:
            payload: dict[str, Any] = {"event": event}
            # Best-effort: extract filename + mode for open events.
            if kind is EventKind.FILE_OPEN and args:
                payload["path"] = str(args[0])
                if len(args) >= 2:
                    payload["mode"] = str(args[1])
            elif kind is EventKind.NETWORK and args:
                payload["target"] = str(args[0])[:200]
            elif kind is EventKind.IMPORT and args:
                payload["module"] = str(args[0])
            self._push(
                TraceEvent(
                    kind=kind,
                    t_ns=time.perf_counter_ns(),
                    payload=payload,
                    depth=self._depth,
                )
            )
        finally:
            _local.in_tracer = False

    # ----- buffer management

    def _push(self, ev: TraceEvent) -> None:
        self.buffer.append(ev)
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                listener(ev)
        if len(self.buffer) > self.max_buffer:
            self._spill()

    def _spill(self) -> None:
        if self.spill_path is None:
            # Bounded ring: drop the oldest half.
            for _ in range(self.max_buffer // 2):
                self.buffer.popleft()
                self._spilled += 1
            return
        # Spill the oldest half to disk as JSONL.
        n = self.max_buffer // 2
        chunk = [self.buffer.popleft() for _ in range(n)]
        self._spilled += n
        self.spill_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        with self.spill_path.open("a", encoding="utf-8") as f:
            for ev in chunk:
                f.write(
                    json.dumps(
                        {
                            "kind": ev.kind.value,
                            "t_ns": ev.t_ns,
                            "qualname": ev.func_qualname,
                            "module": ev.func_module,
                            "depth": ev.depth,
                            "payload": ev.payload,
                        }
                    )
                    + "\n"
                )

    # ----- query

    def events(self) -> Iterable[TraceEvent]:
        return tuple(self.buffer)

    def spilled_count(self) -> int:
        return self._spilled


def _safe_module(code: CodeType) -> str | None:
    # Some compiled code has no associated module name.
    try:
        return code.co_filename and os.path.basename(code.co_filename)
    except Exception:
        return None
