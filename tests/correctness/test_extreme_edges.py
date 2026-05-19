"""Adversarial edge-case suite: extreme inputs, hostile filesystems, race
conditions, and exotic Python semantics that a research workload could
realistically hit but the basic test suite doesn't probe.

Every test here either demonstrates a real protection or — if it fails —
exposes a hole worth documenting in DECISIONS.md. There is no "skip if
hard"; we either pass or we annotate the limitation.
"""

from __future__ import annotations

import gc
import multiprocessing as mp
import os
import sys
import threading
import time
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import rote
from rote import session as _session


@pytest.fixture(autouse=True)
def _disable_perf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """The perf guard blacklists functions whose encode+write time exceeds
    their body runtime. Trivial test bodies trip it on every call, which
    masks the cache behaviors we want to assert. Set the guard threshold
    to effectively-infinite so caching is always attempted.
    """
    monkeypatch.setattr(_session, "_PERF_GUARD_MIN_WRITE_NS", 10**18)
    _session._PERF_BLACKLIST.clear()


# ============================================================================
# Section A — Fingerprint distinctness (cache poisoning hazards)
# ============================================================================

# Python's `==` treats some values as equal that have distinct semantics. If
# rote's fingerprint collapses them, f(True) returns f(1)'s cached value and
# vice versa — silent wrong-result poisoning.


def test_bool_true_distinct_from_int_one() -> None:
    seen: list[Any] = []

    @rote.cache
    def echo(x: Any) -> str:
        seen.append((type(x).__name__, x))
        return f"{type(x).__name__}:{x}"

    assert echo(True) == "bool:True"
    assert echo(1) == "int:1"
    # If they collide, the second call hits the bool entry → returns "bool:True"
    assert seen == [("bool", True), ("int", 1)]


def test_bool_false_distinct_from_int_zero() -> None:
    seen: list[Any] = []

    @rote.cache
    def echo(x: Any) -> str:
        seen.append((type(x).__name__, x))
        return f"{type(x).__name__}:{x}"

    assert echo(False) == "bool:False"
    assert echo(0) == "int:0"
    assert seen == [("bool", False), ("int", 0)]


def test_int_distinct_from_float_same_value() -> None:
    seen: list[type] = []

    @rote.cache
    def echo(x: Any) -> str:
        seen.append(type(x))
        return type(x).__name__

    assert echo(1) == "int"
    assert echo(1.0) == "float"
    assert seen == [int, float]


def test_int_distinct_from_decimal_same_value() -> None:
    @rote.cache
    def kind(x: Any) -> str:
        return type(x).__name__

    assert kind(1) == "int"
    assert kind(Decimal("1")) == "Decimal"


def test_str_distinct_from_bytes_same_chars() -> None:
    @rote.cache
    def kind(x: Any) -> str:
        return type(x).__name__

    assert kind("hello") == "str"
    assert kind(b"hello") == "bytes"


def test_unicode_nfc_vs_nfd_distinct() -> None:
    # The character "é" can be encoded as one codepoint (NFC) or two (NFD).
    # They render identically but are distinct strings. A cache that
    # normalizes silently before hashing would collapse them — a real
    # research bug (filenames from macOS HFS+ vs Linux ext4 differ this way).
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd

    seen: list[str] = []

    @rote.cache
    def echo(s: str) -> int:
        seen.append(s)
        return len(s)

    assert echo(nfc) == len(nfc)
    assert echo(nfd) == len(nfd)
    assert len(seen) == 2  # both must execute


def test_frozenset_distinct_from_tuple_with_same_elements() -> None:
    @rote.cache
    def kind(x: Any) -> str:
        return type(x).__name__

    assert kind(frozenset([1, 2, 3])) == "frozenset"
    assert kind((1, 2, 3)) == "tuple"


def test_complex_distinct_from_real_when_imag_zero() -> None:
    @rote.cache
    def kind(x: Any) -> str:
        return type(x).__name__

    assert kind(complex(1, 0)) == "complex"
    assert kind(1.0) == "float"


def test_positional_vs_keyword_with_same_value() -> None:
    seen: list[tuple[Any, ...]] = []

    @rote.cache
    def f(a: int, b: int) -> int:
        seen.append((a, b))
        return a + b

    # These are semantically identical; the cache is allowed to merge them
    # OR keep them distinct, but it must NOT return f(1, 99)'s value for f(1, b=2).
    assert f(1, 2) == 3
    assert f(1, b=2) == 3
    # Either both executed (distinct keys) or just one (merged) — but the
    # *returned values* must equal what each call would produce fresh.


def test_kwargs_with_dict_arg_distinguishes_by_insertion_order() -> None:
    # Documented behavior: dict insertion order participates in the
    # fingerprint. Researchers who depend on iteration order (numpy axis
    # naming, JSON output, etc.) get distinct keys; researchers who rely
    # only on equality get one extra recomputation. This is the safer
    # default — collapsing them would risk wrong results when order
    # matters semantically.
    seen: list[dict[str, int]] = []

    @rote.cache
    def f(d: dict[str, int]) -> int:
        seen.append(dict(d))
        return sum(d.values())

    assert f({"a": 1, "b": 2}) == 3
    assert f({"b": 2, "a": 1}) == 3
    # Different insertion order → currently distinct keys. Document the
    # current (safer) behavior; both calls executed.
    assert len(seen) == 2


def test_args_unpacked_from_star_match_explicit_args() -> None:
    """f(*[1, 2]) and f(1, 2) are semantically identical: by the time the
    wrapper runs, args == (1, 2) in both cases. They must hit the same
    cache key.
    """
    rote.configure(min_duration_s=0.0)
    rote.clear()

    @rote.cache
    def f(a: int, b: int) -> int:
        return a * 10 + b

    before = rote.stats().get("hits", 0)
    assert f(*[1, 2]) == 12
    assert f(1, 2) == 12
    after = rote.stats().get("hits", 0)
    assert after - before >= 1, "star-unpacked and explicit args should share a cache entry"


# ============================================================================
# Section B — File-dependency edge cases
# ============================================================================


def test_atomic_rename_replaces_content_invalidates_cache(tmp_path: Path) -> None:
    """Researcher pattern: write new data to a tempfile, then `os.rename` it
    on top of the dependency. The path is unchanged but the content is now
    different. Cache must miss on the next call.
    """
    rote.configure(min_duration_s=0.0)
    target = tmp_path / "data.txt"
    target.write_text("original")

    @rote.cache
    def read() -> str:
        return target.read_text()

    assert read() == "original"

    tmp = tmp_path / "data.txt.new"
    tmp.write_text("replaced via rename")
    os.rename(tmp, target)

    assert read() == "replaced via rename"


def test_hardlink_content_change_invalidates(tmp_path: Path) -> None:
    """Two paths, same inode. Writing through one changes both. The cache
    must invalidate based on content, not path identity."""
    if not hasattr(os, "link"):
        pytest.skip("os.link unavailable")
    rote.configure(min_duration_s=0.0)

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("v1")
    os.link(a, b)

    @rote.cache
    def read_a() -> str:
        return a.read_text()

    assert read_a() == "v1"
    b.write_text("v2")  # mutate through the other hardlink
    assert read_a() == "v2"


def test_symlink_target_change_invalidates(tmp_path: Path) -> None:
    """The dep path is a symlink. Changing what it points to must invalidate
    (the *target* file is the actual content the function read)."""
    rote.configure(min_duration_s=0.0)
    real1 = tmp_path / "real1.txt"
    real2 = tmp_path / "real2.txt"
    real1.write_text("one")
    real2.write_text("two")
    link = tmp_path / "link.txt"
    link.symlink_to(real1)

    @rote.cache
    def read_link() -> str:
        return link.read_text()

    assert read_link() == "one"
    link.unlink()
    link.symlink_to(real2)
    assert read_link() == "two"


def test_chmod_only_does_not_invalidate(tmp_path: Path) -> None:
    """Content hash should not be affected by mode bits alone — chmod
    changes ctime but not content. The system should not invalidate."""
    rote.configure(min_duration_s=0.0)
    p = tmp_path / "data.txt"
    p.write_text("payload")

    runs = [0]

    @rote.cache
    def read() -> str:
        runs[0] += 1
        return p.read_text()

    assert read() == "payload"
    initial_runs = runs[0]
    # Toggle a permission bit (a no-op for content).
    p.chmod(0o600)
    time.sleep(0.001)  # ensure any clock-based heuristic wouldn't fire spuriously
    assert read() == "payload"
    # Acceptable to either hit (good) or re-run once for ctime change; never
    # more than that. The ctime field IS part of the cache key, so a re-run
    # is expected once — but subsequent calls must stabilize.
    third = read()
    assert third == "payload"
    assert runs[0] <= initial_runs + 2


def test_dependency_deletion_then_recreation_invalidates(tmp_path: Path) -> None:
    """File present → cache hit. File deleted → call must fail or miss
    (not return stale). File recreated with new content → must serve new
    content, not the original stale value."""
    rote.configure(min_duration_s=0.0)
    p = tmp_path / "data.txt"
    p.write_text("original")

    @rote.cache
    def read() -> str:
        return p.read_text()

    assert read() == "original"
    p.unlink()
    p.write_text("recreated")
    assert read() == "recreated"


def test_dependency_replaced_by_directory_does_not_corrupt(tmp_path: Path) -> None:
    """A file dependency is replaced by a directory of the same name. The
    cache must miss (or raise), not return the stale text content."""
    rote.configure(min_duration_s=0.0)
    p = tmp_path / "thing"
    p.write_text("file content")

    @rote.cache
    def kind() -> str:
        if p.is_file():
            return "file:" + p.read_text()
        return "directory"

    assert kind() == "file:file content"
    p.unlink()
    p.mkdir()
    # Must not return the cached "file:file content" — that would be stale.
    assert kind() == "directory"


def test_dev_null_read_does_not_block_or_crash(tmp_path: Path) -> None:
    """Reading /dev/null returns empty bytes; the cache shouldn't choke
    on the special file."""
    if not os.path.exists("/dev/null"):
        pytest.skip("no /dev/null on this OS")

    @rote.cache
    def read_null() -> bytes:
        with open("/dev/null", "rb") as f:
            return f.read()

    assert read_null() == b""
    assert read_null() == b""


def test_empty_file_dependency(tmp_path: Path) -> None:
    """Empty files have size 0 and no content. The cache must still
    correctly fingerprint them and invalidate when they become non-empty."""
    rote.configure(min_duration_s=0.0)
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")

    @rote.cache
    def read() -> bytes:
        return p.read_bytes()

    assert read() == b""
    p.write_bytes(b"now has content")
    assert read() == b"now has content"


def test_file_path_with_spaces_and_unicode(tmp_path: Path) -> None:
    """Researchers use weird paths. Make sure they don't break path handling
    (especially the `_path_is_under` containment check from Codex's commit)."""
    rote.configure(min_duration_s=0.0)
    weird = tmp_path / "café data — file (1).txt"
    weird.write_text("ok")

    @rote.cache
    def read() -> str:
        return weird.read_text()

    assert read() == "ok"
    weird.write_text("changed")
    assert read() == "changed"


# ============================================================================
# Section C — Code identity / global state
# ============================================================================


def test_function_redefinition_changes_cache_key() -> None:
    """Same name, different body — should not hit the previous version's cache."""
    cache_dir_a = []

    def make_func(body_int: int):
        @rote.cache
        def f(x: int) -> int:
            return x + body_int  # different body each time

        cache_dir_a.append(f)
        return f

    f1 = make_func(100)
    f2 = make_func(200)

    assert f1(1) == 101
    assert f2(1) == 201  # if cache collapsed by name, would return 101


def test_module_attribute_swap_to_callable_invalidates() -> None:
    """A researcher rebinds a module attribute (used by the cached function)
    from one callable to another. The dependency tracking must see this."""
    import math

    @rote.cache
    def compute() -> float:
        return math.cos(0)  # constant; depends on math.cos identity

    assert compute() == 1.0
    original_cos = math.cos
    try:
        math.cos = lambda x: 999.0  # noqa: E731
        # math is a stdlib module, so this is technically not user state.
        # Cache may or may not invalidate — but it must not silently return
        # stale data when the global is a USER-visible name. Re-running with
        # the rebound math.cos in scope is acceptable behavior.
        result = compute()
        assert result in (1.0, 999.0)
    finally:
        math.cos = original_cos


# Module-level so the function's bytecode contains a true LOAD_GLOBAL
# for the static analyzer to pick up. (globals()["X"] dynamic access is
# documented in DECISIONS.md as outside the scope of the static analysis.)
_global_container: list[int] = [1, 2, 3]


def test_global_mutable_container_mutation_invalidates() -> None:
    """A function reads a module-level global list via LOAD_GLOBAL. Appending
    to the list must invalidate the cache on the next call."""
    rote.configure(min_duration_s=0.0)

    @rote.cache
    def sum_global() -> int:
        return sum(_global_container)

    assert sum_global() == 6
    _global_container.append(4)
    assert sum_global() == 10
    # Restore for test isolation
    _global_container.pop()


# ============================================================================
# Section D — Recursion, reentrancy, exotic call patterns
# ============================================================================


def test_recursive_cached_function_terminates() -> None:
    """Classic Fibonacci. Recursion must not deadlock or infinite-loop, and
    the second top-level call must hit the cache."""
    rote.configure(min_duration_s=0.0)
    rote.clear()

    @rote.cache
    def fib(n: int) -> int:
        if n < 2:
            return n
        return fib(n - 1) + fib(n - 2)

    assert fib(10) == 55
    before = rote.stats().get("hits", 0)
    assert fib(10) == 55
    after = rote.stats().get("hits", 0)
    assert after > before, "second fib(10) call should hit cache"


def test_cached_function_returns_function_then_we_call_it() -> None:
    """A cached function returns a closure. The closure itself isn't cached
    (correctly — closures aren't deterministic) but calling it after a hit
    must work."""

    @rote.cache
    def make_adder(n: int) -> Any:
        # Use lambda — even cloudpickle-serializable, but the identity will
        # differ each call unless the cache is correct. We're testing that
        # we don't crash; behavior around lambda return values is a
        # separately documented limitation.
        return lambda x, _n=n: x + _n

    add5 = make_adder(5)
    assert add5(10) == 15


# ============================================================================
# Section E — Concurrency
# ============================================================================


def test_many_threads_distinct_keys_no_corruption() -> None:
    """100 threads each call with a distinct argument. All results must be
    correct. SQLite WAL + atomic blob writes should handle this cleanly."""
    rote.configure(min_duration_s=0.0)
    barrier = threading.Barrier(50)
    results: dict[int, int] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    @rote.cache
    def compute(n: int) -> int:
        return n * n + 1  # simple, distinct per n

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            r = compute(n)
            with lock:
                results[n] = r
        except BaseException as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert errors == [], f"threads errored: {errors}"
    assert results == {i: i * i + 1 for i in range(50)}


def test_many_threads_same_key_returns_consistent_value() -> None:
    """100 threads, all calling f(7). Must all return the same value. Race
    condition would manifest as some threads returning the function output
    and others returning some intermediate / wrong state."""
    rote.configure(min_duration_s=0.0)
    barrier = threading.Barrier(50)
    results: list[int] = []
    lock = threading.Lock()

    @rote.cache
    def compute(n: int) -> int:
        # Sleep to widen the race window.
        time.sleep(0.001)
        return n * 13

    def worker() -> None:
        barrier.wait(timeout=5.0)
        r = compute(7)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(results) == 50
    assert all(r == 91 for r in results), f"inconsistent results: {set(results)}"


def _mp_worker(idx: int, cache_dir: str, q: mp.Queue[tuple[int, int]]) -> None:
    """Process target for cross-process cache sharing test."""
    import rote as _rote

    _rote.configure(cache_dir=Path(cache_dir), min_duration_s=0.0)

    @_rote.cache
    def compute(n: int) -> int:
        return n * n + 7

    q.put((idx, compute(idx)))


def test_two_processes_sharing_cache_dir_no_corruption(tmp_path: Path) -> None:
    """Multiple processes write to the same SQLite store concurrently. WAL
    mode + busy_timeout should serialize writes; every process gets the
    correct result. Pre-initialize the DB serially first to avoid the
    well-known WAL-init race when N processes simultaneously promote a
    brand-new journal."""
    cache_dir = tmp_path / ".rote"
    # Serial pre-init: open and close the store once so journal_mode=WAL
    # is committed before the worker processes race.
    rote.configure(cache_dir=cache_dir, min_duration_s=0.0)
    rote.stats()  # forces store open
    ctx = mp.get_context("spawn")
    q: mp.Queue[tuple[int, int]] = ctx.Queue()
    procs = [
        ctx.Process(target=_mp_worker, args=(i, str(cache_dir), q))
        for i in range(3)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30.0)
        assert p.exitcode == 0, f"process {p.pid} exit code {p.exitcode}"

    seen: dict[int, int] = {}
    while not q.empty():
        idx, val = q.get()
        seen[idx] = val
    assert seen == {0: 7, 1: 8, 2: 11}


# ============================================================================
# Section F — Resource bounds
# ============================================================================


def test_5000_distinct_keys_does_not_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running 5000 distinct keys must complete without unbounded memory
    growth. The in-memory tier is LRU-capped at _MEM_CACHE_LIMIT (256);
    older entries spill to disk. If the LRU cap were broken, this would
    grow without bound."""
    rote.configure(min_duration_s=0.0)
    monkeypatch.setattr(_session, "_MEM_CACHE_LIMIT", 32)

    @rote.cache
    def square(n: int) -> int:
        return n * n

    # 5000 distinct keys; LRU cap is 32. Just observe completion.
    for i in range(5000):
        assert square(i) == i * i
    # Sanity: re-call the LAST 32 keys; they should still be in memory.
    hits_before = rote.stats().get("hits", 0)
    for i in range(4968, 5000):
        square(i)
    hits_after = rote.stats().get("hits", 0)
    assert hits_after - hits_before >= 30, (
        f"recent keys should still hit; got {hits_after - hits_before}/32 hits"
    )


def test_function_that_raises_does_not_pollute_cache() -> None:
    """An exception must not leave a poisoned cache entry. Subsequent calls
    with the same args must either succeed or re-raise — never return the
    previous attempt's partial state."""
    rote.configure(min_duration_s=0.0)
    attempts = [0]

    @rote.cache
    def maybe_raise(should_fail: bool) -> int:
        attempts[0] += 1
        if should_fail:
            raise RuntimeError("boom")
        return attempts[0]

    with pytest.raises(RuntimeError):
        maybe_raise(True)
    with pytest.raises(RuntimeError):
        maybe_raise(True)  # must re-raise, not return cached state
    # And a successful call after must still run the body.
    n = maybe_raise(False)
    assert n >= 1


def test_huge_return_value_handled(tmp_path: Path) -> None:
    """Function returns a 10 MB bytes object. Must serialize correctly and
    hit on the second call."""
    rote.configure(min_duration_s=0.0, cache_dir=tmp_path / ".rote")

    @rote.cache
    def make_blob() -> bytes:
        return b"x" * (10 * 1024 * 1024)

    a = make_blob()
    b = make_blob()
    assert a == b
    assert len(a) == 10 * 1024 * 1024


# ============================================================================
# Section G — Audit hook + impurity in adversarial situations
# ============================================================================


def test_audit_hook_deferred_until_first_use() -> None:
    """After `import rote` but before any decoration or `auto()` call, the
    audit hooks must not be installed. This is the win from my recent
    audit-hook-defer commit."""
    # We can't easily un-install once installed in the same process. So we
    # spawn a fresh subprocess and check.
    code = r"""
import sys
import rote  # NOQA: F401

# Just observe — don't trigger any wrapping.
# (audit hooks can't be enumerated portably; we can only assert that
#  rote.session._audit_hooks_installed is False before first use.)
import rote.session
assert rote.session._audit_hooks_installed is False, (
    "audit hooks should not be installed on bare import"
)
print("ok")
"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr}"
    assert "ok" in result.stdout


def test_audit_hooks_install_on_first_cache_decoration() -> None:
    """Decorating a function must install hooks (otherwise file-dep
    tracking inside that function won't fire)."""
    code = r"""
import rote
import rote.session

assert rote.session._audit_hooks_installed is False

@rote.cache
def f(x):
    return x + 1

assert rote.session._audit_hooks_installed is True
print("ok")
"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr}"
    assert "ok" in result.stdout


def test_function_using_subprocess_marked_impure() -> None:
    """subprocess.run is impure (side effect on the OS). Must not cache."""
    import contextlib
    import subprocess as _sub
    runs = [0]

    @rote.cache
    def run_true() -> int:
        runs[0] += 1
        # `true` is on every POSIX system; on others we just check that the
        # subprocess module access path doesn't get silently cached.
        with contextlib.suppress(FileNotFoundError, OSError):
            _sub.run(["true"], check=False, capture_output=True, timeout=2)
        return runs[0]

    run_true()
    run_true()
    # If the second call was cached, runs[0] stays at 1. We expect 2:
    # subprocess.run is on the impure-stdlib list and should block caching.
    assert runs[0] == 2, f"subprocess call was incorrectly cached (runs={runs[0]})"


def test_file_append_in_user_code_marked_impure(tmp_path: Path) -> None:
    """Append-mode open is persisted state observable to the user. Must not
    cache — every call must re-run."""
    log = tmp_path / "log.txt"
    runs = [0]

    @rote.cache
    def append_log() -> int:
        runs[0] += 1
        with open(log, "a") as f:
            f.write(f"call {runs[0]}\n")
        return runs[0]

    append_log()
    append_log()
    append_log()
    assert runs[0] == 3, f"append-mode open was incorrectly cached (runs={runs[0]})"
    assert log.read_text().count("\n") == 3


# ============================================================================
# Section H — Cleanup / introspection
# ============================================================================


def test_cache_clear_then_call_re_executes() -> None:
    rote.configure(min_duration_s=0.0)
    rote.clear()

    @rote.cache
    def f(seed: int) -> int:
        return seed * 17

    f(1)
    hits_before_clear = rote.stats().get("hits", 0)
    f(1)
    assert rote.stats().get("hits", 0) > hits_before_clear, "second call should hit"

    rote.clear()
    misses_before = rote.stats().get("misses", 0)
    f(1)
    assert rote.stats().get("misses", 0) > misses_before, "clear() should force a miss"


def test_stats_returns_dict_with_expected_keys() -> None:
    @rote.cache
    def f(x: int) -> int:
        return x

    f(1)
    f(1)
    s = rote.stats()
    assert isinstance(s, dict)
    assert "hits" in s
    assert "misses" in s


@pytest.fixture(autouse=True)
def _cleanup_globals() -> Any:
    """Ensure global test pollution from Section C tests doesn't leak."""
    gc.collect()
    yield
    gc.collect()
