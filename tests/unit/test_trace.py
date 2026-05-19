"""Layer 1 unit tests."""

from __future__ import annotations

from rote.trace import EventKind, Tracer


def _balanced_for(events, names):
    """Count call/return events for a set of named functions; must balance."""
    counts = {n: [0, 0] for n in names}  # [calls, returns]
    for ev in events:
        q = ev.func_qualname or ""
        # Match by leaf name (qualname may include enclosing test name)
        leaf = q.rsplit(".", 1)[-1]
        if leaf in counts:
            if ev.kind == EventKind.CALL:
                counts[leaf][0] += 1
            elif ev.kind in (EventKind.RETURN, EventKind.UNWIND):
                counts[leaf][1] += 1
    return counts


def test_tracer_captures_call_return_pair():
    t = Tracer()
    t.start()
    try:
        def f():
            return 42

        f()
    finally:
        t.stop()
    qns = [ev.func_qualname for ev in t.events() if ev.kind in (EventKind.CALL, EventKind.RETURN)]
    assert "f" in qns or any("test_tracer" in q for q in qns)


def test_tracer_balanced_on_nested_calls():
    t = Tracer()
    t.start()
    try:
        def a():
            return b() + c()

        def b():
            return 1

        def c():
            return 2

        assert a() == 3
    finally:
        t.stop()
    counts = _balanced_for(t.events(), {"a", "b", "c"})
    for name, (calls, returns) in counts.items():
        assert calls == returns >= 1, f"{name}: {calls} calls / {returns} returns"


def test_tracer_captures_raise():
    t = Tracer()
    t.start()
    raised = False
    try:
        def boom():
            raise ValueError("nope")

        try:
            boom()
        except ValueError:
            raised = True
    finally:
        t.stop()
    assert raised
    kinds = [ev.kind for ev in t.events()]
    assert (
        EventKind.UNWIND in kinds or EventKind.RAISE in kinds
    ), f"expected UNWIND/RAISE in kinds, got {set(kinds)}"


def test_tracer_audit_captures_open(tmp_path):
    t = Tracer()
    t.start()
    p = tmp_path / "x.txt"
    try:
        p.write_text("hi")
        p.read_text()
    finally:
        t.stop()
    file_events = [ev for ev in t.events() if ev.kind == EventKind.FILE_OPEN]
    paths = [ev.payload.get("path") for ev in file_events]
    assert any(str(p) in (pp or "") for pp in paths), f"got paths: {paths}"


def test_tracer_can_restart():
    t = Tracer()
    t.start()
    t.stop()
    t.start()
    try:
        def f():
            return 1

        f()
    finally:
        t.stop()
    assert any(ev.kind == EventKind.CALL for ev in t.events())


def test_listener_receives_events():
    t = Tracer()
    received: list[str] = []
    t.add_listener(lambda ev: received.append(ev.kind.value))
    t.start()
    try:
        def f():
            return 1

        f()
    finally:
        t.stop()
    assert any(k == "call" for k in received)


def test_buffer_spills_when_full(tmp_path):
    spill = tmp_path / "spill.jsonl"
    t = Tracer(max_buffer=8, spill_path=spill)
    t.start()
    try:
        for _ in range(100):
            (lambda: 1)()
    finally:
        t.stop()
    assert t.spilled_count() > 0
    assert spill.exists()
    assert spill.stat().st_size > 0
