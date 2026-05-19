"""Layer 5 — Integration. Public API surface.

* :func:`cache` — explicit decorator. Always memoizes the decorated function.
* :func:`auto` — context manager. Inside the ``with`` block, the tracer is
  active and *every* call passing the duration + purity bar is memoized.
* :func:`invalidate` — manual cache busting.
* :func:`graph` — NetworkX DiGraph of observed call dependencies.
* :func:`stats` — hits/misses/time-saved telemetry.
* :func:`clear` — wipe the whole cache.
"""

from __future__ import annotations

import functools
import io
import json
import logging
import os.path as _os_path
import sys
import sysconfig as _sysconfig
import threading as _threading
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from .config import get_config
from .identity import (
    cache_key,
    composite_id,
    hexkey,
    transitive_function_ids,
)
from .purity import (
    PurityTracker,
    args_changed,
    file_dep_hash,
    mutable_value_names,
)
from .serialize import decode, encode, fingerprint
from .store import Store
from .trace import Tracer

_inflight: _threading.local = _threading.local()
_inflight_writes: _threading.local = _threading.local()


def _stack() -> list[list[str]]:
    """Return this thread's in-flight file-reads stack, creating it on demand."""
    s = getattr(_inflight, "stack", None)
    if s is None:
        s = []
        _inflight.stack = s
    return s


def _write_stack() -> list[list[str]]:
    """Return this thread's in-flight file-writes stack."""
    s = getattr(_inflight_writes, "stack", None)
    if s is None:
        s = []
        _inflight_writes.stack = s
    return s


# PEP 578 events that signal a file open. We accept variants emitted by
# pathlib.Path.open, io.open, os.open, and other stdlib wrappers — each
# carries the path as args[0] and (for `open`/`io.open`) the mode as args[1].
_OPEN_EVENTS = frozenset({"open", "builtins.open", "io.open", "os.open"})
_WRITE_MODE_CHARS = frozenset("wax+")


def _audit_record_file_io(event: str, args: tuple[Any, ...]) -> None:
    """Record file reads AND writes into the per-thread in-flight stacks.

    Hot path: the truthiness checks below short-circuit for every audit
    event when no memoized call is in flight.

    Reads → ``_inflight.stack`` (cache key dep — invalidate if file changes).
    Self-contained writes (`w`/`x` mode, not append) → ``_inflight_writes.stack``
    (cache invalidates downstream callers that read the written file).
    Append-mode → handled separately by ``_audit_check_impurity``.
    """
    stack = getattr(_inflight, "stack", None)
    wstack = getattr(_inflight_writes, "stack", None)
    if (not stack and not wstack) or event not in _OPEN_EVENTS or not args:
        return
    path = args[0]
    mode = args[1] if len(args) > 1 else "r"
    try:
        from os.path import abspath

        path_str = abspath(
            str(path) if not isinstance(path, (str, bytes))
            else (path.decode() if isinstance(path, bytes) else path)
        )
    except Exception:
        return
    is_read = True
    if isinstance(mode, str):
        if "a" in mode:
            return  # append handled by impurity hook
        if any(c in mode for c in ("w", "x")):
            is_read = False
    elif isinstance(mode, int):
        if mode & 0o3:  # O_WRONLY or O_RDWR
            is_read = False
    if is_read and stack:
        stack[-1].append(path_str)
    elif not is_read and wstack:
        wstack[-1].append(path_str)


sys.addaudithook(_audit_record_file_io)


# Per-call impurity reasons collected for the in-flight memoized call. Each
# entry corresponds to the matching slot in ``_inflight.stack`` — i.e., if
# the file-read stack has 3 frames, this stack has 3 lists of strings.
_inflight_impurity: _threading.local = _threading.local()


def _impurity_stack() -> list[list[str]]:
    s = getattr(_inflight_impurity, "stack", None)
    if s is None:
        s = []
        _inflight_impurity.stack = s
    return s


def _audit_check_impurity(event: str, args: tuple[Any, ...]) -> None:
    """Mark every in-flight memoized call impure when an audit event signals
    network, exec, or append-mode file open.

    Skips events originating inside library code (stdlib + site-packages):
    those are the library's internal mechanism, not the user function's
    observable side effect. Append-open is always honored regardless of
    origin because it represents persisted state.
    """
    stack = getattr(_inflight_impurity, "stack", None)
    if not stack:
        return
    reason: str | None = None
    is_append = False
    if event.startswith(("socket.", "urllib.", "http.client.", "ftplib.", "smtplib.")):
        reason = f"network I/O: {event}"
    elif event in ("exec", "compile"):
        reason = f"exec/compile: {event}"
    elif event in _OPEN_EVENTS and args:
        mode = args[1] if len(args) > 1 else "r"
        if isinstance(mode, str) and "a" in mode:
            reason = f"append-mode open: {args[0]!r}"
            is_append = True
    if reason is None:
        return
    # File appends always count — they persist state observable to the user.
    if not is_append and _audit_caller_in_library():
        return
    for frame_reasons in stack:
        frame_reasons.append(reason)


def _is_library_filename(fn: str) -> bool:
    """True if a frame's filename refers to library code we want to ignore.

    Includes our own package, stdlib + site-packages paths (resolving
    symlinks), and any bracket-form pseudo-filename
    (``<frozen importlib._bootstrap>``, ``<string>``, ``<built-in>``).
    """
    if not fn:
        return True
    if fn.startswith("<") and fn.endswith(">"):
        return True
    if fn.startswith(_ROTE_PATH_PREFIX) or fn.startswith(_LIB_PATH_PREFIXES):
        return True
    # Resolve symlinks lazily — handles Homebrew's
    # /opt/homebrew/opt/python@X.Y → /opt/homebrew/Cellar/python@X.Y/X.Y.Z
    # symlink chain that sysconfig doesn't always normalize.
    real = _os_path.realpath(fn)
    return real.startswith(_ROTE_PATH_PREFIX) or real.startswith(_LIB_PATH_PREFIXES)


def _audit_caller_in_library() -> bool:
    """Walk a few frames up to see if the audit event originated inside
    library code. Cheap because we stop at the first user-code frame."""
    f: Any = sys._getframe(2)  # skip this helper + _audit_check_impurity
    for _ in range(8):
        if f is None:
            return True
        if not _is_library_filename(f.f_code.co_filename):
            return False  # found a user frame
        f = f.f_back
    return True


sys.addaudithook(_audit_check_impurity)


# Lightweight sys.monitoring tool for the @cache decorator path. Uses
# TOOL_ID 5 (DEBUGGER slot) — leaves slot 4 (PROFILER) free for the full
# Tracer used by auto(). The callback is enabled only while at least one
# decorated function is in flight on the current thread, so processes that
# never use @cache pay zero overhead.
_DECORATOR_TOOL_ID = 5
_decorator_tool_active = False
_decorator_tool_lock = _threading.Lock()


# Absolute path prefix of this package. Calls *into* our own infrastructure
# (cache writes, serialization, etc.) MUST NOT propagate impurity to the
# user's wrapper — tempfile.mkstemp et al. are implementation details.
_ROTE_PATH_PREFIX = _os_path.realpath(_os_path.dirname(_os_path.abspath(__file__)))


def _collect_lib_prefixes() -> tuple[str, ...]:
    """Resolve every Python install/site-packages root to its canonical path.

    Includes sys.prefix and sys.base_prefix because Homebrew/pyenv installs
    symlink the canonical name to a versioned cellar path, and `sysconfig`
    returns the symlink target inconsistently across helpers.
    """
    candidates: set[str] = set()
    for p in (
        _sysconfig.get_path("stdlib"),
        _sysconfig.get_path("platstdlib"),
        _sysconfig.get_path("purelib"),
        _sysconfig.get_path("platlib"),
        sys.prefix,
        sys.base_prefix,
    ):
        if not p:
            continue
        candidates.add(_os_path.realpath(p))
        candidates.add(p)  # also keep the unresolved form
    return tuple(candidates)


_LIB_PATH_PREFIXES: tuple[str, ...] = _collect_lib_prefixes()

# Thread-local nesting counter for "we're inside rote infrastructure".
# Incremented around store.put / encode / etc. The PY_START callback
# returns early when this is non-zero.
_infra_depth: _threading.local = _threading.local()


def _enter_infra() -> None:
    _infra_depth.n = getattr(_infra_depth, "n", 0) + 1


def _leave_infra() -> None:
    _infra_depth.n = getattr(_infra_depth, "n", 1) - 1


def _path_is_under(path: str, root: str) -> bool:
    try:
        real_path = _os_path.realpath(path)
        real_root = _os_path.realpath(root)
        return _os_path.commonpath((real_path, real_root)) == real_root
    except (OSError, ValueError):
        return False


def _audit_check_callee_purity(code: Any, instruction_offset: int) -> object:
    """PY_START callback: flag calls to impure stdlib symbols.

    Skips calls originating from inside our own package (filename prefix)
    and skips calls made while the infra-depth counter is positive (we're
    in the middle of a cache write or similar).
    """
    if getattr(_infra_depth, "n", 0) > 0:
        return None
    stack = getattr(_inflight_impurity, "stack", None)
    if not stack:
        return None
    filename = code.co_filename
    if _is_library_filename(filename):
        return None
    from . import _impure_stdlib

    qualname = code.co_qualname
    module = filename.rsplit("/", 1)[1].removesuffix(".py") if "/" in filename else filename.removesuffix(".py")
    full = f"{module}.{qualname}" if module else qualname
    if _impure_stdlib.is_impure(full):
        reason = f"calls impure stdlib: {full}"
        for frame_reasons in stack:
            frame_reasons.append(reason)
    return None


def _enable_decorator_purity_monitor() -> None:
    """Install the per-call PY_START callback if not already active."""
    global _decorator_tool_active
    with _decorator_tool_lock:
        if _decorator_tool_active:
            return
        mon = sys.monitoring
        existing = mon.get_tool(_DECORATOR_TOOL_ID)
        if existing not in (None, "rote-decorator"):
            # Another tool owns the slot — silently skip the impurity-callee check.
            return
        mon.use_tool_id(_DECORATOR_TOOL_ID, "rote-decorator")
        mon.set_events(_DECORATOR_TOOL_ID, mon.events.PY_START)
        mon.register_callback(_DECORATOR_TOOL_ID, mon.events.PY_START, _audit_check_callee_purity)
        _decorator_tool_active = True


def _disable_decorator_purity_monitor() -> None:
    """Uninstall the PY_START callback when the in-flight stack is empty."""
    global _decorator_tool_active
    with _decorator_tool_lock:
        if not _decorator_tool_active:
            return
        mon = sys.monitoring
        try:
            mon.register_callback(_DECORATOR_TOOL_ID, mon.events.PY_START, None)
            mon.set_events(_DECORATOR_TOOL_ID, 0)
            mon.free_tool_id(_DECORATOR_TOOL_ID)
        except (RuntimeError, ValueError):
            pass
        _decorator_tool_active = False


def _global_dep_names(func: Callable[..., Any]) -> list[str]:
    """Names referenced by ``func`` that resolve to fingerprintable globals.

    These are the "globally-reachable Python values that the function reads"
    from paper §3.4. Each call fingerprints the current value of these
    globals and folds them into the cache key, so editing a module-level
    constant invalidates the cache.

    Missing names are included so a failed first call does not permanently
    omit a late-bound global from the cache key.
    """
    import dis

    code = getattr(func, "__code__", None)
    if code is None:
        return []
    out: set[str] = set()
    for instr in dis.get_instructions(code):
        if instr.opname != "LOAD_GLOBAL" or not isinstance(instr.argval, str):
            continue
        name = instr.argval
        # Filter out things that look like sentinel/internal names.
        if name.startswith("__") and name.endswith("__"):
            continue
        out.add(name)
    return sorted(out)


def _module_attr_dep_names(func: Callable[..., Any]) -> list[tuple[str, str, str]]:
    import dis

    code = getattr(func, "__code__", None)
    if code is None:
        return []
    out: set[tuple[str, str, str]] = set()
    instrs = list(dis.get_instructions(code))
    for i, instr in enumerate(instrs[:-1]):
        if instr.opname not in ("LOAD_GLOBAL", "LOAD_DEREF", "LOAD_CLASSDEREF"):
            continue
        next_instr = instrs[i + 1]
        if next_instr.opname not in ("LOAD_ATTR", "LOAD_METHOD"):
            continue
        if not isinstance(instr.argval, str) or not isinstance(next_instr.argval, str):
            continue
        scope = "global" if instr.opname == "LOAD_GLOBAL" else "closure"
        out.add((scope, instr.argval, next_instr.argval))
    return sorted(out)


def _callable_dep_fingerprint(value: Any) -> bytes:
    from types import BuiltinFunctionType, BuiltinMethodType, FunctionType, MethodType

    unwrapped = value
    seen: set[int] = set()
    while hasattr(unwrapped, "__wrapped__") and id(unwrapped) not in seen:
        seen.add(id(unwrapped))
        unwrapped = unwrapped.__wrapped__

    parts: list[bytes] = []
    if isinstance(unwrapped, MethodType):
        parts.append(b"method")
        parts.append(transitive_function_ids(unwrapped.__func__))
        self_obj = getattr(unwrapped, "__self__", None)
        if self_obj is not None:
            parts.append(b"self=" + fingerprint(self_obj))
    elif isinstance(unwrapped, FunctionType):
        parts.append(b"function")
        parts.append(transitive_function_ids(unwrapped))
    elif isinstance(unwrapped, (BuiltinFunctionType, BuiltinMethodType)):
        module = getattr(unwrapped, "__module__", None) or ""
        qualname = getattr(unwrapped, "__qualname__", None) or getattr(unwrapped, "__name__", repr(unwrapped))
        parts.append(f"builtin:{module}.{qualname}".encode())
        self_obj = getattr(unwrapped, "__self__", None)
        import inspect

        if self_obj is not None and not inspect.ismodule(self_obj):
            parts.append(b"self=" + fingerprint(self_obj))
    else:
        parts.append(fingerprint(unwrapped))
    return composite_id(*parts)


def _dependency_value_fingerprint(value: Any) -> bytes:
    if _is_function_like(value):
        return _callable_dep_fingerprint(value)
    return fingerprint(value)


def _global_deps_fingerprint(
    func: Callable[..., Any],
    names: list[str],
    module_attr_deps: list[tuple[str, str, str]],
) -> bytes:
    """Stable fingerprint of referenced globals and closure cells.

    Uses the same fingerprint() machinery as args. If a global is
    unhashable / unserializable, falls back to id() — guaranteed cache miss
    on that path, not a crash.
    """
    import inspect

    g = getattr(func, "__globals__", {}) or {}
    parts: list[bytes] = []
    for name in names:
        if name in g:
            val = g[name]
            if inspect.ismodule(val):
                module_name = getattr(val, "__name__", "")
                parts.append(b"global:" + name.encode() + b"=<module:" + module_name.encode() + b">")
            else:
                parts.append(b"global:" + name.encode() + b"=" + _dependency_value_fingerprint(val))
        else:
            parts.append(b"global:" + name.encode() + b"=<missing>")
    code = getattr(func, "__code__", None)
    closure = getattr(func, "__closure__", None) or ()
    closure_map: dict[str, Any] = {}
    if code is not None:
        for name, cell in zip(code.co_freevars, closure, strict=False):
            val = _safe_cell(cell)
            if val is _MISSING:
                parts.append(b"closure:" + name.encode() + b"=<missing>")
                continue
            closure_map[name] = val
            if inspect.ismodule(val):
                continue
            parts.append(b"closure:" + name.encode() + b"=" + _dependency_value_fingerprint(val))
    for scope, owner_name, attr_name in module_attr_deps:
        owner = g.get(owner_name, _MISSING) if scope == "global" else closure_map.get(owner_name, _MISSING)
        label = f"module_attr:{scope}:{owner_name}.{attr_name}".encode()
        if owner is _MISSING:
            parts.append(label + b"=<missing-owner>")
            continue
        if not inspect.ismodule(owner):
            continue
        try:
            attr_val = getattr(owner, attr_name)
        except AttributeError:
            parts.append(label + b"=<missing>")
            continue
        if inspect.ismodule(attr_val):
            module_name = getattr(attr_val, "__name__", "")
            parts.append(label + b"=<module:" + module_name.encode() + b">")
        else:
            parts.append(label + b"=" + _dependency_value_fingerprint(attr_val))
    if not parts:
        return b""
    from .serialize import _hash  # type: ignore[attr-defined]

    return _hash(b"\0".join(parts))


def _static_impure_callees(func: Callable[..., Any]) -> list[str]:
    """Walk ``func``'s bytecode for references to impure stdlib symbols.

    PY_START callbacks miss C builtins (``time.time``, ``random.random``,
    ``os.listdir`` — all C-implemented). Static bytecode analysis catches
    them by examining ``LOAD_GLOBAL`` + ``LOAD_ATTR`` sequences and
    direct global references.

    Returns a list of impurity reasons (empty if statically pure).
    """
    import dis
    import inspect

    code = getattr(func, "__code__", None)
    if code is None:
        return []
    g = getattr(func, "__globals__", {}) or {}
    from . import _impure_stdlib

    # Resolve a name through globals, then through closure cells if available.
    closure_names = code.co_freevars
    closure = getattr(func, "__closure__", None) or ()
    closure_map: dict[str, Any] = {
        name: cell.cell_contents
        for name, cell in zip(closure_names, closure, strict=False)
        if _safe_cell(cell) is not _MISSING
    }

    def _resolve(name: str) -> Any:
        if name in g:
            return g[name]
        return closure_map.get(name)

    reasons: set[str] = set()
    instrs = list(dis.get_instructions(code))
    for i, instr in enumerate(instrs):
        if instr.opname == "LOAD_GLOBAL" and instr.argval == "getattr":
            if i + 2 >= len(instrs):
                continue
            owner_instr = instrs[i + 1]
            attr_instr = instrs[i + 2]
            if owner_instr.opname not in ("LOAD_GLOBAL", "LOAD_DEREF", "LOAD_CLASSDEREF"):
                continue
            owner = _resolve(owner_instr.argval)
            if owner is None:
                continue
            module_name = getattr(owner, "__name__", None) if inspect.ismodule(owner) else None
            base = module_name or str(owner_instr.argval)
            if attr_instr.opname == "LOAD_CONST" and isinstance(attr_instr.argval, str):
                full = f"{base}.{attr_instr.argval}"
                if _impure_stdlib.is_impure(full):
                    reasons.add(f"calls impure stdlib: {full}")
            elif inspect.ismodule(owner) and _impure_stdlib.is_impure(f"{base}.__dynamic__"):
                reasons.add(f"dynamic getattr from impure stdlib: {base}")
            continue
        if instr.opname not in ("LOAD_GLOBAL", "LOAD_DEREF", "LOAD_CLASSDEREF"):
            continue
        name = instr.argval
        target = _resolve(name)
        if target is None:
            continue
        # Pattern A: LOAD ... X; LOAD_ATTR/LOAD_METHOD Y → "<module>.Y"
        # where <module> is the resolved target's actual ``__name__``, not
        # the local alias. This catches ``import time as _t; _t.time()`` too.
        if i + 1 < len(instrs) and instrs[i + 1].opname in ("LOAD_ATTR", "LOAD_METHOD"):
            attr = instrs[i + 1].argval
            # Prefer the resolved module's own name; fall back to the alias.
            module_name = getattr(target, "__name__", None) if inspect.ismodule(target) else None
            base = module_name or name
            full = f"{base}.{attr}"
            if _impure_stdlib.is_impure(full):
                reasons.add(f"calls impure stdlib: {full}")
            continue
        # Pattern B: bare LOAD of a callable from an impure module
        # (e.g. ``from time import time; time()``).
        if inspect.ismodule(target):
            continue
        modname = getattr(target, "__module__", None)
        qualname = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
        if modname and qualname:
            full = f"{modname}.{qualname}"
            if _impure_stdlib.is_impure(full):
                reasons.add(f"calls impure stdlib: {full}")
    return sorted(reasons)


_MISSING = object()


def _is_function_like(value: Any) -> bool:
    from types import BuiltinFunctionType, BuiltinMethodType, FunctionType, MethodType

    unwrapped = value
    seen: set[int] = set()
    while hasattr(unwrapped, "__wrapped__") and id(unwrapped) not in seen:
        seen.add(id(unwrapped))
        unwrapped = unwrapped.__wrapped__
    return isinstance(
        unwrapped,
        (FunctionType, MethodType, BuiltinFunctionType, BuiltinMethodType),
    )


def _safe_cell(cell: Any) -> Any:
    try:
        return cell.cell_contents
    except ValueError:
        return _MISSING


def _signature_state(func: Callable[..., Any]) -> tuple[int, int, int]:
    return (
        id(getattr(func, "__defaults__", None)),
        id(getattr(func, "__kwdefaults__", None)),
        id(getattr(func, "__signature__", None)),
    )


def _safe_signature(func: Callable[..., Any]) -> Any | None:
    import inspect

    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def _signature_cache(func: Callable[..., Any]) -> list[Any]:
    return [_signature_state(func), _safe_signature(func)]


def _bound_call_values(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    signature_cache: list[Any] | None = None,
) -> dict[str, Any]:
    sig = None
    if signature_cache is not None:
        state = _signature_state(func)
        if state != signature_cache[0]:
            signature_cache[0] = state
            signature_cache[1] = _safe_signature(func)
        sig = signature_cache[1]
    else:
        sig = _safe_signature(func)
    if sig is not None:
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            return dict(bound.arguments)
        except TypeError:
            pass
    values = {f"arg{i}": a for i, a in enumerate(args)}
    values.update(kwargs)
    return values


def _fingerprint_values(values: dict[str, Any]) -> dict[str, bytes]:
    return {k: fingerprint(v) for k, v in values.items()}

log = logging.getLogger("rote")

P = ParamSpec("P")
R = TypeVar("R")

# Per-wrapper in-memory hit-cache size. Bounded LRU; oldest entries dropped
# when the limit is reached. Chosen so the typical research script's working
# set fits but a long-running session can't consume unbounded memory.
_MEM_CACHE_LIMIT = 256

# Per-session blacklist of function IDs whose encode+write time exceeded
# their run time on the first miss (paper §3.3.2). Once blacklisted, the
# wrapper still runs the function on every call but skips the cache write
# — the cure was worse than the disease.
_PERF_BLACKLIST: set[bytes] = set()
_PERF_GUARD_MIN_WRITE_NS = 5_000_000


def _mem_set(
    mem: OrderedDict[bytes, tuple[Any, str, str, int, list[str], bytes | None]],
    key: bytes,
    value: tuple[Any, str, str, int, list[str], bytes | None],
) -> None:
    """Insert into the per-wrapper LRU, evicting the oldest entry if over limit."""
    mem[key] = value
    if len(mem) > _MEM_CACHE_LIMIT:
        mem.popitem(last=False)


# ---------------------------------------------------------------- Telemetry


@dataclass
class SessionStats:
    """In-memory hit/miss counters for the current session."""

    hits: int = 0
    misses: int = 0
    impure_skips: int = 0
    too_fast_skips: int = 0
    too_big_skips: int = 0
    saved_ns: int = 0
    spent_ns: int = 0
    invalidation_reasons: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "impure_skips": self.impure_skips,
            "too_fast_skips": self.too_fast_skips,
            "too_big_skips": self.too_big_skips,
            "saved_seconds": self.saved_ns / 1e9,
            "spent_seconds": self.spent_ns / 1e9,
            "invalidation_reasons": dict(self.invalidation_reasons),
        }


@dataclass
class _Session:
    """Process-global mutable state — exactly one exists."""

    tracer: Tracer | None = None
    purity: PurityTracker | None = None
    store: Store | None = None
    stats: SessionStats = field(default_factory=SessionStats)
    call_graph: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    telemetry_path: Path | None = None
    _stack: list[str] = field(default_factory=list)

    def ensure_store(self) -> Store:
        if self.store is None:
            cfg = get_config()
            self.store = Store(cfg.cache_dir, fsync_writes=cfg.fsync_writes)
        return self.store

    def reset_session(self) -> None:
        if self.purity is not None:
            self.purity.close()
            self.purity = None
        if self.tracer is not None:
            self.tracer.stop()
            self.tracer = None


_session = _Session()


def _get_session() -> _Session:
    return _session


# ------------------------------------------------------------------ Decorator


class _Tee:
    """Write to two streams; used to capture stdout during a memoized call.

    The captured copy goes into a StringIO so it can be replayed on cache hit.
    The mirror still receives writes so the user sees progress in real time.
    """

    def __init__(self, primary: Any, mirror: io.StringIO) -> None:
        self._primary = primary
        self._mirror = mirror

    def write(self, data: str) -> int:
        self._mirror.write(data)
        try:
            n = self._primary.write(data)
            return int(n) if n is not None else len(data)
        except Exception:
            return len(data)

    def flush(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._primary.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)


def _async_cache(func: Callable[..., Any]) -> Callable[..., Any]:
    """Async counterpart to ``cache`` — handles ``async def`` functions.

    Mirrors the sync wrapper exactly: same key derivation, same store
    interactions, same stdout/stderr replay. The only difference is the
    body of the wrapped function is awaited and we return a coroutine.
    """
    qualname = getattr(func, "__qualname__", repr(func))
    mem_cache: OrderedDict[
        bytes, tuple[Any, str, str, int, list[str], bytes | None]
    ] = OrderedDict()
    signature_cache = _signature_cache(func)
    cached_fid: bytes | None = None
    static_impurity: list[str] | None = None
    static_impurity_digest: bytes | None = None
    global_dep_names: list[str] | None = None
    module_attr_dep_names: list[tuple[str, str, str]] | None = None

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        nonlocal cached_fid, static_impurity, static_impurity_digest, global_dep_names, module_attr_dep_names
        cfg = get_config()
        sess = _get_session()
        store = sess.ensure_store()
        if cached_fid is None:
            cached_fid = transitive_function_ids(func)
            global_dep_names = _global_dep_names(func)
            module_attr_dep_names = _module_attr_dep_names(func)
        fid = cached_fid

        call_values = _bound_call_values(func, args, kwargs, signature_cache)
        arg_fps = _fingerprint_values(call_values)
        from .identity import composite_id

        # Include parameter names with their fingerprints so f(1,0) and
        # f(0,1) hash differently — values alone collide on permutations.
        input_digest = composite_id(
            *(k.encode() + b"=" + v for k, v in sorted(arg_fps.items()))
        )
        global_digest = _global_deps_fingerprint(
            func, global_dep_names or [], module_attr_dep_names or []
        )
        if static_impurity is None or static_impurity_digest != global_digest:
            static_impurity = _static_impure_callees(func)
            static_impurity_digest = global_digest
        key = cache_key(fid, input_digest, b"", global_digest)

        cached_tuple = mem_cache.get(key)
        if cached_tuple is not None:
            ret_val, stdout_buf, stderr_buf, dur_ns, deps, stored_dep_hash = cached_tuple
            if not deps or stored_dep_hash == file_dep_hash(deps):
                mem_cache.move_to_end(key)
                if stdout_buf:
                    sys.stdout.write(stdout_buf)
                if stderr_buf:
                    sys.stderr.write(stderr_buf)
                sess.stats.hits += 1
                sess.stats.saved_ns += dur_ns
                store.hit(key, eager=cfg.eager_hit_counters)
                return ret_val
            del mem_cache[key]

        hit_row = store.get_fast(key)
        if hit_row is not None:
            ser_name, stored_dep_hash, deps, write_deps, hit_key, dur_ns = hit_row
            # The hash covers BOTH read deps and write deps: a missing
            # write output (someone deleted the file the cached call
            # produced) must miss so we re-run and recreate it.
            combined_deps = sorted(set(deps) | set(write_deps))
            if not combined_deps or stored_dep_hash == file_dep_hash(combined_deps):
                payload = store.get_payload(hit_key)
                if payload is not None:
                    try:
                        cached = decode(ser_name, payload)
                    except Exception as exc:
                        log.warning("decode failed on %s: %s", qualname, exc)
                        cached = None
                    if isinstance(cached, dict) and "_rote_v" in cached:
                        ret_val = cached["return"]
                        out_s = cached.get("stdout", "")
                        err_s = cached.get("stderr", "")
                        if out_s:
                            sys.stdout.write(out_s)
                        if err_s:
                            sys.stderr.write(err_s)
                        _mem_set(mem_cache, key, (ret_val, out_s, err_s, dur_ns, combined_deps, stored_dep_hash))
                        sess.stats.hits += 1
                        sess.stats.saved_ns += dur_ns
                        store.hit(hit_key, eager=cfg.eager_hit_counters)
                        return ret_val
                    elif cached is not None:
                        _mem_set(mem_cache, key, (cached, "", "", dur_ns, combined_deps, stored_dep_hash))
                        sess.stats.hits += 1
                        sess.stats.saved_ns += dur_ns
                        store.hit(hit_key, eager=cfg.eager_hit_counters)
                        return cached

        # Miss path — run the coroutine while capturing output.
        sess.stats.misses += 1
        t0 = time.perf_counter_ns()
        before = dict(arg_fps)
        names_to_recheck = mutable_value_names(call_values)
        out_buf, err_buf = io.StringIO(), io.StringIO()
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(orig_out, out_buf), _Tee(orig_err, err_buf)
        stack = _stack()
        wstack = _write_stack()
        impurity_stack = _impurity_stack()
        stack.append([])
        wstack.append([])
        impurity_stack.append([])
        if len(impurity_stack) == 1:
            _enable_decorator_purity_monitor()
        try:
            result = await func(*args, **kwargs)
        finally:
            sys.stdout, sys.stderr = orig_out, orig_err
            recorded_reads = stack.pop()
            recorded_writes = wstack.pop()
            impurity_reasons = impurity_stack.pop()
            if len(impurity_stack) == 0:
                _disable_decorator_purity_monitor()
        if names_to_recheck:
            after = {**before}
            for k in names_to_recheck:
                after[k] = fingerprint(call_values[k])
        else:
            after = before
        elapsed_ns = time.perf_counter_ns() - t0
        sess.stats.spent_ns += elapsed_ns
        changed = args_changed(before, after)
        if changed:
            sess.stats.impure_skips += 1
            sess.stats.invalidation_reasons[f"mutated:{','.join(changed)}"] += 1
            return result
        if impurity_reasons or static_impurity:
            sess.stats.impure_skips += 1
            for r in (impurity_reasons + (static_impurity or [])):
                sess.stats.invalidation_reasons[r] += 1
            return result
        if elapsed_ns < int(cfg.min_duration_s * 1e9):
            sess.stats.too_fast_skips += 1
            return result
        if cfg.read_only:
            return result

        captured_out, captured_err = out_buf.getvalue(), err_buf.getvalue()
        to_encode: Any
        if captured_out or captured_err:
            to_encode = {
                "_rote_v": 1,
                "return": result,
                "stdout": captured_out,
                "stderr": captured_err,
            }
        else:
            to_encode = result
        # Paper §3.3.2: skip caching if the function is on the per-session
        # perf blacklist (encode+write previously dwarfed its run time).
        if fid in _PERF_BLACKLIST:
            sess.stats.invalidation_reasons["perf_blacklist"] += 1
            return result
        _enter_infra()
        write_t0 = time.perf_counter_ns()
        try:
            try:
                ser_name, payload = encode(to_encode)
            except Exception as exc:
                sess.stats.invalidation_reasons[f"encode_failure:{type(exc).__name__}"] += 1
                log.warning("encode failed for %s: %s", qualname, exc)
                return result
            if len(payload) > cfg.max_value_bytes:
                sess.stats.too_big_skips += 1
                return result
            cache_root = str(cfg.cache_dir)
            file_deps = sorted({p for p in recorded_reads if not _path_is_under(p, cache_root)})
            write_deps = sorted({p for p in recorded_writes if not _path_is_under(p, cache_root)})
            # Hash covers reads AND writes — a deleted/edited write output
            # must invalidate the entry so the next call re-creates it.
            combined_for_hash = sorted(set(file_deps) | set(write_deps))
            dep_hash = file_dep_hash(combined_for_hash) if combined_for_hash else None
            store.put(
                key=key,
                function_name=qualname,
                serializer=ser_name,
                payload=payload,
                file_dependencies=file_deps,
                file_dep_hash=dep_hash,
                file_write_dependencies=write_deps,
                code_dependencies=[hexkey(fid)],
                run_duration_ns=elapsed_ns,
            )
        finally:
            _leave_infra()
        write_ns = time.perf_counter_ns() - write_t0
        # Adaptive guard: if writing this cache entry took longer than the
        # function itself, we'll never recoup the cost. Blacklist the
        # function ID and warn once.
        if write_ns > elapsed_ns and write_ns >= _PERF_GUARD_MIN_WRITE_NS and elapsed_ns > 0:
            _PERF_BLACKLIST.add(fid)
            log.warning(
                "rote: blacklisting %s — encode+write %.1fms > run %.1fms",
                qualname, write_ns / 1e6, elapsed_ns / 1e6,
            )
            sess.stats.invalidation_reasons["perf_blacklist_added"] += 1
        _mem_set(mem_cache, key, (result, captured_out, captured_err, elapsed_ns, combined_for_hash, dep_hash))
        return result

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper


def cache[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Explicit decorator: every call is memoized (subject to purity).

    Captures stdout + stderr written during the call and replays them on a
    cache hit. This lets us correctly memoize functions whose observable
    behavior includes print-side-effects.

    Works regardless of decorator ordering relative to ``@classmethod`` /
    ``@staticmethod``: if either is the inner decorator, we unwrap, memoize
    the underlying function, then re-wrap so the descriptor protocol still
    holds on the class.
    """
    # Handle classmethod / staticmethod ordering. If user wrote
    # ``@rote.cache @classmethod def f(...)``, ``func`` is the descriptor,
    # not a plain function. Unwrap, recursively cache the underlying function,
    # then re-wrap so the descriptor protocol still works on the class.
    if isinstance(func, (classmethod, staticmethod)):
        inner = func.__func__
        wrapped_inner = cache(inner)  # type: ignore[arg-type]
        return type(func)(wrapped_inner)  # type: ignore[return-value]

    # Async functions return coroutines; we have to ``await`` them inside
    # the wrapper and return an async-compatible callable. Delegate to a
    # dedicated path so the sync hot path stays small.
    import asyncio

    if asyncio.iscoroutinefunction(func):
        return _async_cache(func)  # type: ignore[return-value]

    qualname = getattr(func, "__qualname__", repr(func))
    mem_cache: OrderedDict[
        bytes, tuple[Any, str, str, int, list[str], bytes | None]
    ] = OrderedDict()
    signature_cache = _signature_cache(func)
    # Computed lazily on first call so siblings have a chance to be wrapped
    # in their globals before we walk transitively.
    cached_fid: bytes | None = None
    # Names of global variables this function reads (per paper §3.4) —
    # their current values get folded into the cache key on every call.
    global_dep_names: list[str] | None = None
    module_attr_dep_names: list[tuple[str, str, str]] | None = None
    # Static-analysis impurity reasons computed lazily too. If non-empty, the
    # wrapper never writes to cache (the function statically references an
    # impure stdlib symbol that runtime monitoring would miss).
    static_impurity: list[str] | None = None
    static_impurity_digest: bytes | None = None

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        nonlocal cached_fid, static_impurity, static_impurity_digest, global_dep_names, module_attr_dep_names
        cfg = get_config()
        sess = _get_session()
        store = sess.ensure_store()

        if cached_fid is None:
            cached_fid = transitive_function_ids(func)
            global_dep_names = _global_dep_names(func)
            module_attr_dep_names = _module_attr_dep_names(func)
        fid = cached_fid

        call_values = _bound_call_values(func, args, kwargs, signature_cache)
        arg_fps = _fingerprint_values(call_values)
        from .identity import composite_id

        # Include parameter names with their fingerprints so f(1,0) and
        # f(0,1) hash differently — values alone collide on permutations.
        input_digest = composite_id(
            *(k.encode() + b"=" + v for k, v in sorted(arg_fps.items()))
        )
        # Fold the current values of referenced globals into the cache key
        # so editing a module-level constant invalidates per paper §3.4.
        global_digest = _global_deps_fingerprint(
            func, global_dep_names or [], module_attr_dep_names or []
        )
        if static_impurity is None or static_impurity_digest != global_digest:
            static_impurity = _static_impure_callees(func)
            static_impurity_digest = global_digest
        key = cache_key(fid, input_digest, b"", global_digest)

        # ---- Tier 1: in-memory hit cache (no SQLite, no disk, no decode).
        cached_tuple = mem_cache.get(key)
        if cached_tuple is not None:
            ret_val, stdout_buf, stderr_buf, dur_ns, deps, stored_dep_hash = cached_tuple
            # Re-validate file deps against the current filesystem state.
            # Without this, mtime-preserving edits to a tracked file would
            # silently return stale results from the in-process cache.
            if not deps or stored_dep_hash == file_dep_hash(deps):
                mem_cache.move_to_end(key)
                if stdout_buf:
                    sys.stdout.write(stdout_buf)
                if stderr_buf:
                    sys.stderr.write(stderr_buf)
                sess.stats.hits += 1
                sess.stats.saved_ns += dur_ns
                store.hit(key, eager=cfg.eager_hit_counters)
                return ret_val  # type: ignore[no-any-return]
            # File deps changed — invalidate this mem-cache entry and fall through.
            del mem_cache[key]
            sess.stats.invalidation_reasons["file_dep_changed"] += 1

        # ---- Tier 2: SQLite + on-disk blob.
        hit_row = store.get_fast(key)
        if hit_row is not None:
            ser_name, stored_dep_hash, deps, write_deps, hit_key, dur_ns = hit_row
            # The hash covers BOTH read deps and write deps: a missing
            # write output (someone deleted the file the cached call
            # produced) must miss so we re-run and recreate it.
            combined_deps = sorted(set(deps) | set(write_deps))
            if not combined_deps or stored_dep_hash == file_dep_hash(combined_deps):
                payload = store.get_payload(hit_key)
                if payload is not None:
                    try:
                        cached = decode(ser_name, payload)
                    except Exception as exc:
                        log.warning("decode failed on %s: %s", qualname, exc)
                        cached = None
                    if isinstance(cached, dict) and "_rote_v" in cached:
                        ret_val = cached["return"]
                        out_s = cached.get("stdout", "")
                        err_s = cached.get("stderr", "")
                        if out_s:
                            sys.stdout.write(out_s)
                        if err_s:
                            sys.stderr.write(err_s)
                        _mem_set(mem_cache, key, (ret_val, out_s, err_s, dur_ns, combined_deps, stored_dep_hash))
                        sess.stats.hits += 1
                        sess.stats.saved_ns += dur_ns
                        store.hit(hit_key, eager=cfg.eager_hit_counters)
                        return ret_val  # type: ignore[no-any-return]
                    elif cached is not None:
                        _mem_set(mem_cache, key, (cached, "", "", dur_ns, combined_deps, stored_dep_hash))
                        sess.stats.hits += 1
                        sess.stats.saved_ns += dur_ns
                        store.hit(hit_key, eager=cfg.eager_hit_counters)
                        return cached  # type: ignore[no-any-return]
                sess.stats.invalidation_reasons["payload_missing"] += 1
            else:
                sess.stats.invalidation_reasons["file_dep_changed"] += 1

        # Miss — run while capturing stdout/stderr AND file reads.
        sess.stats.misses += 1
        t0 = time.perf_counter_ns()
        before = dict(arg_fps)
        # Determine which args we actually need to re-fingerprint on exit
        # (skip ints, strs, tuples of immutables, etc — they can't have mutated).
        names_to_recheck = mutable_value_names(call_values)
        out_buf, err_buf = io.StringIO(), io.StringIO()
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(orig_out, out_buf), _Tee(orig_err, err_buf)
        stack = _stack()
        wstack = _write_stack()
        impurity_stack = _impurity_stack()
        stack.append([])
        wstack.append([])
        impurity_stack.append([])
        # Activate the per-call PY_START monitor so calls into impure stdlib
        # functions get flagged. Reference-counted by the stack depth.
        if len(impurity_stack) == 1:
            _enable_decorator_purity_monitor()
        try:
            result = func(*args, **kwargs)
        finally:
            sys.stdout, sys.stderr = orig_out, orig_err
            recorded_reads = stack.pop()
            recorded_writes = wstack.pop()
            impurity_reasons = impurity_stack.pop()
            if len(impurity_stack) == 0:
                _disable_decorator_purity_monitor()
        # Re-fingerprint only the args that could possibly have mutated.
        if names_to_recheck:
            after = {**before}
            for k in names_to_recheck:
                after[k] = fingerprint(call_values[k])
        else:
            after = before
        elapsed_ns = time.perf_counter_ns() - t0
        sess.stats.spent_ns += elapsed_ns

        changed = args_changed(before, after)
        if changed:
            sess.stats.impure_skips += 1
            sess.stats.invalidation_reasons[f"mutated:{','.join(changed)}"] += 1
            return result

        # Audit-hook-flagged impurity: network, exec/compile, append-mode open.
        # Per paper §3.3.1, any of these in the live stack disqualify the call.
        if impurity_reasons or static_impurity:
            sess.stats.impure_skips += 1
            for r in (impurity_reasons + (static_impurity or [])):
                sess.stats.invalidation_reasons[r] += 1
            return result

        if elapsed_ns < int(cfg.min_duration_s * 1e9):
            sess.stats.too_fast_skips += 1
            return result

        if cfg.read_only:
            return result

        # Only wrap in a bundle if we actually captured side-effect output.
        # Otherwise encode the return value directly with its native serializer
        # (preserves Arrow IPC / numpy.save fast paths).
        captured_out, captured_err = out_buf.getvalue(), err_buf.getvalue()
        to_encode: Any
        if captured_out or captured_err:
            to_encode = {
                "_rote_v": 1,
                "return": result,
                "stdout": captured_out,
                "stderr": captured_err,
            }
        else:
            to_encode = result
        if fid in _PERF_BLACKLIST:
            sess.stats.invalidation_reasons["perf_blacklist"] += 1
            return result
        # Bracket the cache-write infrastructure (encode + file_dep_hash +
        # store.put) so the per-call purity monitor doesn't see internal
        # calls into tempfile, sqlite, etc., and falsely flag the wrapper.
        _enter_infra()
        write_t0 = time.perf_counter_ns()
        try:
            try:
                ser_name, payload = encode(to_encode)
            except Exception as exc:
                sess.stats.invalidation_reasons[f"encode_failure:{type(exc).__name__}"] += 1
                log.warning("encode failed for %s: %s", qualname, exc)
                return result

            if len(payload) > cfg.max_value_bytes:
                sess.stats.too_big_skips += 1
                return result

            cache_root = str(cfg.cache_dir)
            file_deps = sorted(
                {p for p in recorded_reads if not _path_is_under(p, cache_root)}
            )
            write_deps = sorted(
                {p for p in recorded_writes if not _path_is_under(p, cache_root)}
            )
            combined_for_hash = sorted(set(file_deps) | set(write_deps))
            dep_hash = file_dep_hash(combined_for_hash) if combined_for_hash else None
            store.put(
                key=key,
                function_name=qualname,
                serializer=ser_name,
                payload=payload,
                file_dependencies=file_deps,
                file_dep_hash=dep_hash,
                file_write_dependencies=write_deps,
                code_dependencies=[hexkey(fid)],
                run_duration_ns=elapsed_ns,
            )
        finally:
            _leave_infra()
        write_ns = time.perf_counter_ns() - write_t0
        if write_ns > elapsed_ns and write_ns >= _PERF_GUARD_MIN_WRITE_NS and elapsed_ns > 0:
            _PERF_BLACKLIST.add(fid)
            log.warning(
                "rote: blacklisting %s — encode+write %.1fms > run %.1fms",
                qualname, write_ns / 1e6, elapsed_ns / 1e6,
            )
            sess.stats.invalidation_reasons["perf_blacklist_added"] += 1
        # Populate the in-memory cache so the next call in this process hits memory.
        _mem_set(
            mem_cache, key,
            (result, captured_out, captured_err, elapsed_ns, combined_for_hash, dep_hash),
        )
        return result

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper


# --------------------------------------------------------------- auto() mode


@contextmanager
def auto() -> Iterator[_Session]:
    """Context manager that enables tracing-driven automatic memoization.

    Inside the ``with`` block, every function call observed by the tracer is
    a candidate. Functions whose wall-clock duration exceeds
    :attr:`Config.min_duration_s` and whose purity tracker reports no impurity
    get cached.
    """
    sess = _get_session()
    if sess.tracer is not None:
        # Already inside an auto() block — nesting is a no-op.
        yield sess
        return
    tracer = Tracer(
        spill_path=get_config().cache_dir / "trace_spill.jsonl",
    )
    sess.tracer = tracer
    sess.purity = PurityTracker(tracer)
    sess.ensure_store()

    # Per-call accumulator. Keyed by frame depth so we can match RETURN to CALL.
    call_state: dict[int, tuple[str, int, bytes, dict[str, bytes]]] = {}

    def _on_event(ev: Any) -> None:
        if ev.kind.value == "call":
            # Record entry time + qualname; arg fingerprints handled by decorator path.
            call_state[ev.depth] = (
                ev.func_qualname or "?",
                ev.t_ns,
                b"",
                {},
            )
            sess._stack.append(ev.func_qualname or "?")
            # Build call graph
            if len(sess._stack) >= 2:
                sess.call_graph[sess._stack[-2]].add(sess._stack[-1])
        elif ev.kind.value in ("return", "raise"):
            sess._stack.pop() if sess._stack else None

    tracer.add_listener(_on_event)
    tracer.start()
    try:
        yield sess
    finally:
        tracer.stop()
        if sess.purity is not None:
            sess.purity.close()
        sess.purity = None
        sess.tracer = None
        # Telemetry dump
        if get_config().telemetry:
            _flush_telemetry(sess)
        # We intentionally do NOT close the store here; subsequent imports of
        # cached functions still want to read from it.


def _flush_telemetry(sess: _Session) -> None:
    cfg = get_config()
    out_dir = cfg.cache_dir / "sessions"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time())}.json"
    payload = {
        "stats": sess.stats.as_dict(),
        "call_graph": {k: sorted(v) for k, v in sess.call_graph.items()},
    }
    (out_dir / fname).write_text(json.dumps(payload, indent=2))


# ------------------------------------------------------------ invalidate / graph


def invalidate(target: Callable[..., Any] | str | None = None) -> int:
    """Drop cached entries.

    * ``invalidate()`` → wipe the whole cache (same as :func:`clear`).
    * ``invalidate(func)`` → drop entries whose qualname matches ``func``.
    * ``invalidate("foo.bar")`` → drop entries whose qualname matches the string.
    """
    store = _get_session().ensure_store()
    if target is None:
        return store.clear()
    name = getattr(target, "__qualname__", repr(target)) if callable(target) else str(target)
    return store.delete_function(name)


def clear() -> int:
    """Wipe all cached entries. Returns the number of entries removed."""
    return _get_session().ensure_store().clear()


def graph() -> Any:
    """Return a ``networkx.DiGraph`` of observed caller → callee edges."""
    import networkx as nx

    g = nx.DiGraph()
    sess = _get_session()
    for caller, callees in sess.call_graph.items():
        for callee in callees:
            g.add_edge(caller, callee)
    return g


def stats() -> dict[str, Any]:
    """Return a dict combining session counters and store totals."""
    sess = _get_session()
    out = sess.stats.as_dict()
    store = sess.ensure_store()
    out["store"] = store.stats()
    return out


def _reset_for_testing() -> None:
    """Reset *all* session state. Tests only."""
    sess = _get_session()
    sess.reset_session()
    sess.stats = SessionStats()
    sess.call_graph = defaultdict(set)
    if sess.store is not None:
        sess.store.close()
        sess.store = None
    # Clear thread-local in-flight stacks and uninstall the decorator monitor
    # so a test that crashed mid-call doesn't leak state into the next test.
    if hasattr(_inflight, "stack"):
        _inflight.stack.clear()
    if hasattr(_inflight_writes, "stack"):
        _inflight_writes.stack.clear()
    if hasattr(_inflight_impurity, "stack"):
        _inflight_impurity.stack.clear()
    _disable_decorator_purity_monitor()
    _PERF_BLACKLIST.clear()
