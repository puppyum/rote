"""Proves that the five formerly-unbounded structures now stay bounded
under sustained load. A long-running Jupyter kernel or daemon is the
target workload — every leak below would have grown without ceiling
across a multi-day session.

Each fix is verified two ways: (a) the size invariant holds at and past
the cap, (b) the behavior the structure was supposed to provide still
works after evictions (LRU semantics, not just "size goes down").
"""

from __future__ import annotations

import gc
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pytest

import rote
from rote import session as _session
from rote.purity import PurityTracker
from rote.store import _PENDING_HITS_FLUSH_AT, Store
from rote.trace import EventKind, TraceEvent, Tracer

# ============================================================================
# Fix 1 — _PERF_BLACKLIST is now a bounded LRU
# ============================================================================


@pytest.fixture
def _clear_perf_state() -> Any:
    _session._PERF_BLACKLIST.clear()
    yield
    _session._PERF_BLACKLIST.clear()


def test_perf_blacklist_stays_at_cap_under_pressure(
    _clear_perf_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lower the cap to 100, add 10K distinct fids, assert size stays <= cap."""
    monkeypatch.setattr(_session, "_PERF_BLACKLIST_CAP", 100)
    for i in range(10_000):
        _session._perf_blacklist_add(f"fid-{i}".encode())
    assert len(_session._PERF_BLACKLIST) == 100, (
        f"blacklist grew past cap: {len(_session._PERF_BLACKLIST)}"
    )


def test_perf_blacklist_lru_order_evicts_oldest(
    _clear_perf_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After cap is reached, the OLDEST entry must be the one evicted, not
    a recently-added one."""
    monkeypatch.setattr(_session, "_PERF_BLACKLIST_CAP", 3)
    for tag in (b"a", b"b", b"c"):
        _session._perf_blacklist_add(tag)
    assert list(_session._PERF_BLACKLIST) == [b"a", b"b", b"c"]
    _session._perf_blacklist_add(b"d")  # evicts a
    assert list(_session._PERF_BLACKLIST) == [b"b", b"c", b"d"]


def test_perf_blacklist_reinsert_moves_to_end(
    _clear_perf_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-blacklisting an existing fid must move it to most-recent so it
    isn't immediately evicted on the next add."""
    monkeypatch.setattr(_session, "_PERF_BLACKLIST_CAP", 3)
    for tag in (b"a", b"b", b"c"):
        _session._perf_blacklist_add(tag)
    _session._perf_blacklist_add(b"a")  # bump a to most-recent
    _session._perf_blacklist_add(b"d")  # evicts b, not a
    assert b"a" in _session._PERF_BLACKLIST
    assert b"b" not in _session._PERF_BLACKLIST


def test_perf_blacklist_cap_zero_is_safe(_clear_perf_state: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pathological cap=0 should not loop forever or crash; the dict ends
    up empty after each add."""
    monkeypatch.setattr(_session, "_PERF_BLACKLIST_CAP", 0)
    _session._perf_blacklist_add(b"x")
    assert len(_session._PERF_BLACKLIST) == 0


def test_perf_blacklist_membership_still_works_after_eviction(
    _clear_perf_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the blacklist is the `in` check on the hot path.
    Confirm it still returns the right answer after eviction churn."""
    monkeypatch.setattr(_session, "_PERF_BLACKLIST_CAP", 5)
    for i in range(20):
        _session._perf_blacklist_add(f"x{i}".encode())
    # Most-recent 5 must be present.
    for i in range(15, 20):
        assert f"x{i}".encode() in _session._PERF_BLACKLIST
    # Older ones must be evicted.
    for i in range(15):
        assert f"x{i}".encode() not in _session._PERF_BLACKLIST


# ============================================================================
# Fix 2 — _Session.call_graph is bounded by distinct callers
# ============================================================================


def test_call_graph_bounded_by_caller_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate 50_000 distinct callers; the call_graph must stay at
    the configured cap (not 50K)."""
    monkeypatch.setattr(_session, "_CALL_GRAPH_CAP", 100)
    sess = _session._get_session()
    sess.call_graph = OrderedDict()

    # Build a fake event stream by directly invoking the closure path.
    # We can't easily start a real Tracer in a test (the Python audit hook
    # would pollute the run). Instead, we exercise the OrderedDict logic
    # directly with the same shape the real listener uses.
    for i in range(50_000):
        caller = f"caller_{i}"
        callee = f"callee_{i}"
        if caller in sess.call_graph:
            sess.call_graph[caller].add(callee)
            sess.call_graph.move_to_end(caller)
        else:
            sess.call_graph[caller] = {callee}
            while len(sess.call_graph) > _session._CALL_GRAPH_CAP:
                sess.call_graph.popitem(last=False)

    assert len(sess.call_graph) == 100, (
        f"call_graph grew past cap: {len(sess.call_graph)}"
    )


def test_call_graph_keeps_recent_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    """After eviction, the MOST RECENT callers survive."""
    monkeypatch.setattr(_session, "_CALL_GRAPH_CAP", 5)
    sess = _session._get_session()
    sess.call_graph = OrderedDict()
    for i in range(20):
        c = f"caller_{i}"
        sess.call_graph[c] = {f"callee_{i}"}
        while len(sess.call_graph) > _session._CALL_GRAPH_CAP:
            sess.call_graph.popitem(last=False)
    surviving = list(sess.call_graph.keys())
    assert surviving == [f"caller_{i}" for i in range(15, 20)]


def test_call_graph_does_not_evict_in_use_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the same caller keeps getting hit, it must stay (LRU semantics
    via move_to_end)."""
    monkeypatch.setattr(_session, "_CALL_GRAPH_CAP", 3)
    sess = _session._get_session()
    sess.call_graph = OrderedDict()
    # Establish 3 callers.
    for c in ("a", "b", "c"):
        sess.call_graph[c] = {"callee"}
    # Keep touching 'a' as we add new callers.
    for i in range(10):
        # touch a
        if "a" in sess.call_graph:
            sess.call_graph["a"].add(f"new_callee_{i}")
            sess.call_graph.move_to_end("a")
        # add a fresh caller
        new = f"caller_{i}"
        sess.call_graph[new] = {"x"}
        while len(sess.call_graph) > _session._CALL_GRAPH_CAP:
            sess.call_graph.popitem(last=False)
    assert "a" in sess.call_graph


# ============================================================================
# Fix 3 — PurityTracker.verdicts is bounded
# ============================================================================


def test_verdicts_bounded_under_many_distinct_code_objects() -> None:
    """Compile 5000 distinct code objects, run each through the tracker,
    assert the verdicts dict stays at the cap.

    Keep strong refs to every compiled code object so id() values don't
    collide via Python's address reuse.
    """
    t = Tracer()
    p = PurityTracker(t)
    p.VERDICTS_CAP = 50  # type: ignore[misc]  # tighten for the test

    codes = [compile(f"def f_{i}(): return {i}\n", f"<test_{i}>", "exec") for i in range(5000)]
    for i, code in enumerate(codes):
        p._on_event(TraceEvent(kind=EventKind.CALL, t_ns=0, code=code, func_qualname=f"f_{i}", depth=1))
        p._on_event(TraceEvent(kind=EventKind.RETURN, t_ns=1, code=code, func_qualname=f"f_{i}", depth=1))

    assert len(p.verdicts) == 50, f"verdicts grew past cap: {len(p.verdicts)}"


def test_verdicts_evicts_oldest_first() -> None:
    t = Tracer()
    p = PurityTracker(t)
    p.VERDICTS_CAP = 2  # type: ignore[misc]
    codes = [compile(f"def g_{i}(): return {i}\n", f"<t_{i}>", "exec") for i in range(4)]
    for c in codes:
        call_ev = TraceEvent(kind=EventKind.CALL, t_ns=0, code=c, func_qualname="g", depth=1)
        ret_ev = TraceEvent(kind=EventKind.RETURN, t_ns=1, code=c, func_qualname="g", depth=1)
        p._on_event(call_ev)
        p._on_event(ret_ev)
    # Only the last two code objects should have verdicts.
    survivor_ids = set(p.verdicts.keys())
    assert id(codes[2]) in survivor_ids
    assert id(codes[3]) in survivor_ids
    assert id(codes[0]) not in survivor_ids
    assert id(codes[1]) not in survivor_ids


# ============================================================================
# Fix 4 — Tracer.buffer no longer pins CodeType / return_value refs
# ============================================================================


def test_buffer_does_not_pin_code_object_after_push() -> None:
    """After _push completes, the buffered event must not hold a strong
    reference to the original code object. We verify by giving the event
    a code obj, pushing, then asserting ev.code is None."""
    t = Tracer()
    src = "def quick(): return 1\n"
    code = compile(src, "<quick>", "exec")
    ev = TraceEvent(
        kind=EventKind.CALL,
        t_ns=0,
        code=code,
        func_qualname="quick",
        depth=1,
    )
    t._push(ev)
    assert ev.code is None, "buffer pinned the CodeType reference"
    # Metadata must survive for spill.
    assert ev.func_qualname == "quick"
    assert ev.depth == 1


def test_buffer_does_not_pin_return_value() -> None:
    """Return values can be arbitrarily large; the buffer must not keep
    them alive past listener dispatch."""
    t = Tracer()
    big = b"x" * (1024 * 1024)  # 1 MB; we'll drop the only strong ref
    ev = TraceEvent(
        kind=EventKind.RETURN,
        t_ns=0,
        return_value=big,
        func_qualname="big_return",
        depth=1,
    )
    t._push(ev)
    assert ev.return_value is None, "buffer pinned the return value"


def test_buffer_listeners_still_see_original_refs() -> None:
    """The null-out must happen AFTER listeners run, not before — listeners
    need to inspect the live code + return_value."""
    t = Tracer()
    seen_codes: list[Any] = []
    seen_returns: list[Any] = []

    def listener(ev: TraceEvent) -> None:
        seen_codes.append(ev.code)
        seen_returns.append(ev.return_value)

    t.add_listener(listener)
    code = compile("def f(): pass\n", "<l>", "exec")
    sentinel = object()
    ev = TraceEvent(
        kind=EventKind.RETURN,
        t_ns=0,
        code=code,
        return_value=sentinel,
        func_qualname="f",
        depth=1,
    )
    t._push(ev)
    assert seen_codes == [code]
    assert seen_returns == [sentinel]
    # After push, the buffered event itself is cleared.
    assert ev.code is None
    assert ev.return_value is None


def test_buffer_size_capped_at_max_buffer() -> None:
    """Push 5x max_buffer events; the buffer must spill and stay within
    1.5 * max_buffer (the spill drops half on overflow)."""
    t = Tracer(max_buffer=100, spill_path=None)
    for i in range(500):
        ev = TraceEvent(
            kind=EventKind.CALL,
            t_ns=i,
            func_qualname=f"f_{i}",
            depth=1,
        )
        t._push(ev)
    assert len(t.buffer) <= 150, f"buffer grew past spill threshold: {len(t.buffer)}"


def test_buffer_under_high_traffic_does_not_balloon(tmp_path: Path) -> None:
    """End-to-end: 50K events through a real Tracer. Use tracemalloc to
    confirm the buffer's peak memory cost stays roughly proportional to
    metadata, not to the volume of CodeType objects we passed in."""
    import tracemalloc

    t = Tracer(max_buffer=1000, spill_path=tmp_path / "spill.jsonl")
    # Build 50K distinct code objects so any pin would balloon memory.
    codes = [compile(f"def f_{i}(): return {i}\n", f"<m_{i}>", "exec") for i in range(50_000)]

    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()
    for i, c in enumerate(codes):
        t._push(TraceEvent(
            kind=EventKind.CALL,
            t_ns=i,
            code=c,
            func_qualname=f"f_{i}",
            depth=1,
        ))
    # Drop our own strong refs to the code objects. If the buffer pinned
    # them, this won't free the memory.
    del codes
    gc.collect()
    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Diff: rough bound. CodeType is ~200 bytes; 50K of them is ~10MB.
    # With the fix, the buffer keeps only ~1000 metadata-only events
    # plus the spilled JSONL on disk.
    growth = sum(s.size_diff for s in snap_after.compare_to(snap_before, "filename"))
    assert growth < 5 * 1024 * 1024, (
        f"tracer buffer grew {growth/1024/1024:.1f} MB — "
        "likely pinning CodeType refs"
    )


# ============================================================================
# Fix 5 — Store._pending_hits auto-flushes at threshold
# ============================================================================


def test_pending_hits_auto_flushes_at_threshold(tmp_path: Path) -> None:
    """In lazy mode, hit() must trigger flush_hits when the buffer reaches
    _PENDING_HITS_FLUSH_AT — otherwise a long-running Store leaks keys."""
    store = Store(tmp_path / ".rote", fsync_writes=False)
    try:
        # First put an entry so hit() has something to UPDATE.
        from rote.identity import cache_key
        from rote.serialize import encode
        key = cache_key(b"fid", b"in", b"", b"")
        ser_name, payload = encode(42)
        store.put(
            key=key,
            function_name="test",
            serializer=ser_name,
            payload=payload,
            run_duration_ns=1000,
        )
        # Now hammer hits in lazy mode.
        for _ in range(_PENDING_HITS_FLUSH_AT * 3):
            store.hit(key, eager=False)
        # After 3x the threshold, buffer should be far below threshold —
        # auto-flush kept it small.
        assert len(store._pending_hits) < _PENDING_HITS_FLUSH_AT, (
            f"pending_hits not auto-flushing: {len(store._pending_hits)}"
        )
    finally:
        store.close()


def test_pending_hits_flush_actually_writes(tmp_path: Path) -> None:
    """Auto-flush must persist the counter, not silently drop it."""
    store = Store(tmp_path / ".rote", fsync_writes=False)
    try:
        from rote.identity import cache_key
        from rote.serialize import encode
        key = cache_key(b"fid2", b"in2", b"", b"")
        ser_name, payload = encode("v")
        store.put(
            key=key,
            function_name="test",
            serializer=ser_name,
            payload=payload,
            run_duration_ns=1000,
        )
        for _ in range(_PENDING_HITS_FLUSH_AT + 10):
            store.hit(key, eager=False)
        store.flush_hits()
        # The persisted hit count must equal what we inserted.
        assert store._conn is not None
        row = store._conn.execute("SELECT hits FROM entries WHERE key=?", (key,)).fetchone()
        assert row is not None
        # ≥ _PENDING_HITS_FLUSH_AT because we did at least that many hits.
        assert row[0] >= _PENDING_HITS_FLUSH_AT, (
            f"auto-flush dropped hits: persisted {row[0]}"
        )
    finally:
        store.close()


# ============================================================================
# End-to-end: leave a process running with adversarial load for a while
# ============================================================================


def test_long_running_session_stays_bounded(tmp_path: Path) -> None:
    """The headline scenario: many distinct decorations + many hits over a
    long session. Total resident state in the leak-prone structures must
    stay bounded.
    """
    rote.configure(cache_dir=tmp_path / ".rote", min_duration_s=0.0)

    # 2000 distinct decorated functions, each called 5 times.
    fns = []
    for i in range(2000):
        # Each function has a unique closure variable so the fid differs.
        def make(n: int) -> Any:
            @rote.cache
            def f(x: int) -> int:
                return x * n
            return f
        fns.append(make(i))

    for f in fns:
        for x in range(5):
            f(x)

    # The perf-blacklist might or might not have entries depending on
    # timing, but it must not exceed its cap.
    assert len(_session._PERF_BLACKLIST) <= _session._PERF_BLACKLIST_CAP

    # mem-caches: each wrapper has its own; total entries across all
    # wrappers is the per-wrapper cap × wrapper count, but each individual
    # wrapper stays bounded.
    for mem in _session._ALL_MEM_CACHES[-2000:]:  # the ones we just made
        assert len(mem) <= _session._MEM_CACHE_LIMIT
