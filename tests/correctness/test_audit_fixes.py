"""Regression tests for the audit-driven fixes (P16):
  * Thread-safe in-flight stack
  * Audit-hook impurity propagates to the @cache decorator
  * transitive_function_ids walks closures and default args
  * _function_id_for_code survives id() reuse via weak cache
  * file_dep_hash uses content (not just mtime) for small files
  * Expanded impure-stdlib coverage
  * _is_msgpackable handles cycles
"""

from __future__ import annotations

import threading
import time

import pytest

import rote
from rote import _impure_stdlib


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


# ----- expanded impurity list


def test_glob_marked_impure():
    assert _impure_stdlib.is_impure("glob.glob")


def test_pathlib_glob_marked_impure():
    assert _impure_stdlib.is_impure("pathlib.Path.glob")
    assert _impure_stdlib.is_impure("pathlib.Path.iterdir")


def test_numpy_random_marked_impure():
    assert _impure_stdlib.is_impure("numpy.random.random")


def test_requests_marked_impure():
    assert _impure_stdlib.is_impure("requests.get")
    assert _impure_stdlib.is_impure("httpx.get")


def test_logging_marked_impure():
    assert _impure_stdlib.is_impure("logging.info")


# ----- audit-driven impurity into @cache decorator


def test_decorator_skips_cache_on_network_attempt(tmp_path):
    """A function that opens a TCP socket must NOT be cached."""
    import socket

    @rote.cache
    def fetch():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Connecting to a non-listening port fails fast — that's fine,
            # the AUDIT event fires regardless of connect success.
            s.connect(("127.0.0.1", 1))
        except OSError:
            pass
        finally:
            s.close()
        return 42

    fetch()
    stats = rote.stats()
    assert stats["impure_skips"] >= 1, f"expected impure_skip from socket, got stats={stats}"


def test_decorator_skips_cache_on_append_open(tmp_path):
    """A function that opens a file in append mode must NOT be cached."""
    out = tmp_path / "log.txt"

    @rote.cache
    def append_log(msg):
        with open(out, "a") as f:
            f.write(msg)
        return len(msg)

    append_log("hi")
    stats = rote.stats()
    assert stats["impure_skips"] >= 1, f"expected impure_skip from append, got {stats}"


def test_decorator_skips_cache_on_exec(tmp_path):
    """A function that calls exec must NOT be cached."""

    @rote.cache
    def evaluator(code):
        exec(code, {})
        return "done"

    evaluator("x = 1")
    stats = rote.stats()
    assert stats["impure_skips"] >= 1, f"expected impure_skip from exec, got {stats}"


def test_pure_function_is_still_cached():
    """Sanity: don't over-detect impurity. Pure functions still cache."""

    @rote.cache
    def pure(x):
        return x * 2 + 1

    pure(5)
    pure(5)  # should hit
    stats = rote.stats()
    assert stats["hits"] >= 1


# ----- transitive_function_ids walks closures


def test_closure_change_invalidates(tmp_path):
    """A function that closes over another function must invalidate when the
    closed-over function changes."""
    from rote.identity import transitive_function_ids

    def make_user(helper):
        def user(x):
            return helper(x) + 100
        return user

    def helper_v1(x):
        return x * 10

    def helper_v2(x):
        return x * 999

    u1 = make_user(helper_v1)
    u2 = make_user(helper_v2)
    # The two `user` functions are byte-identical, but their closures differ.
    assert transitive_function_ids(u1) != transitive_function_ids(u2), (
        "closure change went undetected — closed-over helper not hashed"
    )


def test_default_arg_function_change_invalidates():
    from rote.identity import transitive_function_ids

    def h1(x):
        return x + 1

    def h2(x):
        return x + 999

    def f1(g=h1):
        return g(10)

    def f2(g=h2):
        return g(10)

    assert transitive_function_ids(f1) != transitive_function_ids(f2)


# ----- file_dep_hash uses content for small files


def test_mtime_preserving_edit_still_invalidates(tmp_path):
    """If a file's content changes but mtime is preserved (e.g., touch -t),
    content-hash mode must still notice."""
    from rote.purity import file_dep_hash

    p = tmp_path / "small.csv"
    p.write_text("1,2,3\n")
    h1 = file_dep_hash([str(p)])

    # Edit content. Force mtime back to the original to simulate cp -p.
    st = p.stat()
    p.write_text("9,8,7\n")
    import os

    os.utime(p, (st.st_atime, st.st_mtime))
    h2 = file_dep_hash([str(p)])
    assert h1 != h2, "content change with preserved mtime went undetected"


# ----- _is_msgpackable handles cycles


def test_cyclic_list_no_recursion_error():
    """A self-referencing container must not blow the stack."""
    from rote.serialize import _is_msgpackable

    cycle: list = []
    cycle.append(cycle)
    assert _is_msgpackable(cycle) is False  # falls through to cloudpickle


def test_cyclic_dict_no_recursion_error():
    from rote.serialize import _is_msgpackable

    cycle: dict = {}
    cycle["self"] = cycle
    assert _is_msgpackable(cycle) is False


# ----- thread-safe in-flight stack


def test_concurrent_cached_calls_dont_cross_attribute_deps(tmp_path):
    """Two threads each in a @cache call shouldn't see each other's file reads."""
    paths = [tmp_path / f"f{i}.txt" for i in range(2)]
    for i, p in enumerate(paths):
        p.write_text(str(i))

    barrier = threading.Barrier(2)
    results: dict[int, set] = {0: set(), 1: set()}

    def worker(tid):
        @rote.cache
        def read_file(path):
            barrier.wait()  # ensure both threads are inside the wrapper simultaneously
            with open(path) as f:
                content = f.read()
            time.sleep(0.05)
            return content

        results[tid].add(read_file(str(paths[tid])))

    t0 = threading.Thread(target=worker, args=(0,))
    t1 = threading.Thread(target=worker, args=(1,))
    t0.start()
    t1.start()
    t0.join()
    t1.join()

    assert results == {0: {"0"}, 1: {"1"}}, (
        f"thread isolation broken: {results}"
    )


# ----- _function_id_for_code survives exec churn


def test_dynamic_code_objects_get_independent_ids():
    """Even if Python reuses memory addresses for fresh `exec` code objects,
    the per-code cache must not return a stale hash."""
    from rote.identity import function_id

    seen_hashes: set[bytes] = set()
    for i in range(50):
        src = f"def f(x): return x + {i}"
        ns: dict = {}
        exec(src, ns)
        seen_hashes.add(function_id(ns["f"]))
    # 50 different function bodies → 50 distinct hashes.
    assert len(seen_hashes) == 50, (
        f"expected 50 unique hashes, got {len(seen_hashes)} — id() reuse hit"
    )
