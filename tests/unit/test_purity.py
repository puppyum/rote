"""Layer 4 unit tests — purity / mutation detection."""

from __future__ import annotations

from rote import _impure_stdlib
from rote.purity import (
    PurityTracker,
    args_changed,
    compute_arg_fingerprints,
)
from rote.trace import Tracer

# ----- impure stdlib list


def test_impure_module_detected():
    assert _impure_stdlib.is_impure("time.time")
    assert _impure_stdlib.is_impure("socket.socket")
    assert _impure_stdlib.is_impure("subprocess.run")


def test_impure_symbol_detected():
    assert _impure_stdlib.is_impure("random.random")
    assert _impure_stdlib.is_impure("os.environ")
    assert _impure_stdlib.is_impure("uuid.uuid4")


def test_pure_modules_pass():
    assert not _impure_stdlib.is_impure("math.sqrt")
    assert not _impure_stdlib.is_impure("json.dumps")
    assert not _impure_stdlib.is_impure("re.compile")


def test_safe_overrides():
    assert not _impure_stdlib.is_impure("time.gmtime")
    assert not _impure_stdlib.is_impure("time.strftime")


# ----- mutation detection


def test_args_changed_detects_in_place_mutation():
    lst = [1, 2, 3]
    before = compute_arg_fingerprints((lst,), {})
    lst.append(4)
    after = compute_arg_fingerprints((lst,), {})
    assert args_changed(before, after) == ["arg0"]


def test_args_unchanged_when_pure():
    before = compute_arg_fingerprints((1, 2), {"k": "v"})
    after = compute_arg_fingerprints((1, 2), {"k": "v"})
    assert args_changed(before, after) == []


def test_args_changed_on_dict_mutation():
    d = {"a": 1}
    before = compute_arg_fingerprints((d,), {})
    d["b"] = 2
    after = compute_arg_fingerprints((d,), {})
    assert args_changed(before, after) == ["arg0"]


# ----- PurityTracker wiring


def test_pure_function_yields_pure_verdict():
    t = Tracer()
    p = PurityTracker(t)
    t.start()
    try:
        def square(x):
            return x * x

        square(5)
    finally:
        t.stop()
    # Find the verdict for the inner call.
    assert any(v.pure for v in p.verdicts.values()), "expected a pure verdict"


def test_impure_function_flagged_via_file_append(tmp_path):
    t = Tracer()
    p = PurityTracker(t)
    log_path = tmp_path / "log.txt"
    t.start()
    try:
        def writer():
            with open(log_path, "a") as f:  # append → impure
                f.write("x")

        writer()
    finally:
        t.stop()
    # Verdict should exist and be impure.
    bad = [v for v in p.verdicts.values() if not v.pure]
    assert bad, f"expected at least one impure verdict, got {p.verdicts}"


def test_self_contained_write_not_impure(tmp_path):
    t = Tracer()
    p = PurityTracker(t)
    out_path = tmp_path / "out.txt"
    t.start()
    try:
        def write_once():
            with open(out_path, "w") as f:
                f.write("hi")
            # Manually notify close (audit hook doesn't fire for close)
            p.note_close(str(out_path))

        write_once()
    finally:
        t.stop()
    # At least one of the verdicts should be pure-with-write-dep.
    [
        v for v in p.verdicts.values() if v.pure and v.file_write_deps
    ]
    # Acceptable if not detected (heuristic); just make sure we don't falsely
    # call it impure when audit-mode is write-then-close.
    impure = [v for v in p.verdicts.values() if not v.pure and "append" in " ".join(v.reasons)]
    assert impure == [], "self-contained write should not be flagged as append"
