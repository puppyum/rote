"""Regression cases from the Codex paper-faithfulness review."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import rote


def _codex_global_helper(value: int) -> int:
    return value + 1


def _codex_clock_helper() -> float:
    return 1.0


if TYPE_CHECKING:
    _codex_late_global = 0


def _hash_file_dep_worker(path: str, queue: mp.Queue[Any]) -> None:
    from rote.purity import file_dep_hash

    queue.put(file_dep_hash([path]))


def test_omitted_mutable_default_participates_in_cache_key() -> None:
    default: list[str] = []

    @rote.cache
    def count_items(items: list[str] = default) -> int:
        return len(items)

    assert count_items() == 0
    assert count_items() == 0

    default.append("changed")

    assert count_items() == 1


def test_closure_cell_mutation_invalidates_cached_result() -> None:
    def make_reader() -> tuple[Any, dict[str, int]]:
        state = {"value": 1}

        @rote.cache
        def read_value() -> int:
            return state["value"]

        return read_value, state

    read_value, state = make_reader()

    assert read_value() == 1
    assert read_value() == 1

    state["value"] = 2

    assert read_value() == 2


def test_dynamic_getattr_of_impure_stdlib_symbol_is_not_cached() -> None:
    @rote.cache
    def now_ns() -> int:
        return getattr(time, "time_ns")()  # noqa: B009

    first = now_ns()
    time.sleep(0.000001)
    second = now_ns()

    assert second != first
    stats = rote.stats()
    assert stats["hits"] == 0
    assert any("time.time_ns" in reason for reason in stats["invalidation_reasons"])


def test_async_write_dependency_revalidated_from_memory_cache(tmp_path: Path) -> None:
    out_path = tmp_path / "async-output.txt"

    @rote.cache
    async def produce() -> str:
        out_path.write_text("fresh")
        return "ok"

    async def run() -> None:
        assert await produce() == "ok"
        assert await produce() == "ok"
        out_path.unlink()
        assert await produce() == "ok"

    asyncio.run(run())

    assert out_path.read_text() == "fresh"


def test_file_dependency_uses_content_when_size_and_mtime_match(
    tmp_path: Path,
) -> None:
    from rote import purity

    purity._CONTENT_HASH_CACHE.clear()

    data_path = tmp_path / "data.txt"
    data_path.write_bytes(b"aa")
    original_stat = data_path.stat()

    @rote.cache
    def read_bytes(path: Path) -> bytes:
        return path.read_bytes()

    assert read_bytes(data_path) == b"aa"
    assert read_bytes(data_path) == b"aa"

    data_path.write_bytes(b"bb")
    os.utime(data_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    assert read_bytes(data_path) == b"bb"


def test_file_dependency_hash_reuses_unchanged_posix_stat(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    if os.name != "posix":
        pytest.skip("ctime-based content-hash reuse is only enabled on POSIX")

    from rote import purity

    purity._CONTENT_HASH_CACHE.clear()
    data_path = tmp_path / "stable.txt"
    data_path.write_bytes(b"x" * 1024)
    calls = 0
    original_digest = purity._file_content_digest

    def counted_digest(path: Path) -> bytes:
        nonlocal calls
        calls += 1
        return original_digest(path)

    monkeypatch.setattr(purity, "_file_content_digest", counted_digest)

    first = purity.file_dep_hash([str(data_path)])
    second = purity.file_dep_hash([str(data_path)])

    assert first == second
    assert calls == 1


def test_file_dependency_hash_does_not_block_on_fifo(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("fifo test requires os.mkfifo")

    fifo_path = tmp_path / "data.fifo"
    os.mkfifo(fifo_path)
    queue: mp.Queue[Any] = mp.Queue()
    process = mp.Process(target=_hash_file_dep_worker, args=(str(fifo_path), queue))
    process.start()
    process.join(1.0)
    if process.is_alive():
        process.terminate()
        process.join()
    assert not process.is_alive()
    assert queue.get(timeout=1.0)


def test_cache_dir_prefix_sibling_file_dependency_is_tracked(tmp_path: Path) -> None:
    data_path = tmp_path / ".rote-input.txt"
    data_path.write_text("old")
    rote.configure(cache_dir=tmp_path / ".rote", min_duration_s=0.0)

    @rote.cache
    def read_text() -> str:
        return data_path.read_text()

    assert read_text() == "old"
    assert read_text() == "old"

    data_path.write_text("new")

    assert read_text() == "new"


def test_sync_perf_guard_blacklists_when_write_is_slower(monkeypatch: Any) -> None:
    import rote.session as session_mod

    # The conftest autouse fixture disables the perf guard for tests
    # because it masks cache mechanics on slow filesystems. This test is
    # the explicit verification that the guard works, so restore the
    # production default (5 ms).
    monkeypatch.setattr(session_mod, "_PERF_GUARD_MIN_WRITE_NS", 5_000_000)

    original_encode = session_mod.encode

    def slow_encode(value: Any) -> tuple[str, bytes]:
        encoded = original_encode(value)
        time.sleep(0.01)
        return encoded

    monkeypatch.setattr(session_mod, "encode", slow_encode)

    @rote.cache
    def tiny(value: int) -> int:
        return value

    assert tiny(1) == 1

    stats = rote.stats()
    assert session_mod._PERF_BLACKLIST
    assert stats["invalidation_reasons"]["perf_blacklist_added"] == 1


def test_rebound_global_helper_invalidates_cached_result() -> None:
    @rote.cache
    def use_helper(value: int) -> int:
        return _codex_global_helper(value)

    assert use_helper(1) == 2
    assert use_helper(1) == 2

    def replacement(value: int) -> int:
        return value + 10

    original = _codex_global_helper
    try:
        globals()["_codex_global_helper"] = replacement
        assert use_helper(1) == 11
    finally:
        globals()["_codex_global_helper"] = original


def test_rebound_global_helper_to_impure_builtin_is_not_cached() -> None:
    @rote.cache
    def call_helper() -> float:
        return _codex_clock_helper()

    assert call_helper() == 1.0
    assert call_helper() == 1.0

    original = _codex_clock_helper
    try:
        globals()["_codex_clock_helper"] = time.time
        first = call_helper()
        time.sleep(0.001)
        second = call_helper()
    finally:
        globals()["_codex_clock_helper"] = original

    assert first != second
    stats = rote.stats()
    assert any("time.time" in reason for reason in stats["invalidation_reasons"])


def test_rebound_closure_helper_invalidates_cached_result() -> None:
    def make_reader() -> tuple[Any, Any]:
        def helper(value: int) -> int:
            return value + 1

        @rote.cache
        def use_helper(value: int) -> int:
            return helper(value)

        def replace_helper() -> None:
            nonlocal helper

            def replacement(value: int) -> int:
                return value + 10

            helper = replacement

        return use_helper, replace_helper

    use_helper, replace_helper = make_reader()

    assert use_helper(1) == 2
    assert use_helper(1) == 2

    replace_helper()

    assert use_helper(1) == 11


def test_cached_wrapper_reuses_signature_on_hot_path(monkeypatch: Any) -> None:
    import inspect

    def add(value: int, extra: int = 0) -> int:
        return value + extra

    calls = 0
    original_signature = inspect.signature

    def counted_signature(obj: Any) -> Any:
        nonlocal calls
        if obj is add:
            calls += 1
        return original_signature(obj)

    monkeypatch.setattr(inspect, "signature", counted_signature)

    cached = rote.cache(add)

    assert cached(1) == 1
    assert cached(1) == 1
    assert cached(2) == 2
    assert calls == 1


def test_reassigned_defaults_refresh_cached_signature() -> None:
    def read_default(value: int = 1) -> int:
        return value

    cached = rote.cache(read_default)

    assert cached() == 1

    read_default.__defaults__ = (2,)

    assert cached() == 2


def test_late_bound_global_participates_after_initial_name_error() -> None:
    @rote.cache
    def read_late_global() -> int:
        return _codex_late_global

    with pytest.raises(NameError):
        read_late_global()

    try:
        globals()["_codex_late_global"] = 1
        assert read_late_global() == 1
        assert read_late_global() == 1
        globals()["_codex_late_global"] = 2
        assert read_late_global() == 2
    finally:
        globals().pop("_codex_late_global", None)


def test_module_attribute_read_invalidates_cached_result() -> None:
    settings = types.ModuleType("settings")
    settings.VALUE = 1

    @rote.cache
    def read_setting() -> int:
        return settings.VALUE

    assert read_setting() == 1
    assert read_setting() == 1

    settings.VALUE = 2

    assert read_setting() == 2
