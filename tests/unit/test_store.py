"""Layer 3b unit tests — SQLite + blob filesystem."""

from __future__ import annotations

from rote.store import Store


def test_put_then_get_roundtrip(tmp_path):
    s = Store(tmp_path / "c")
    key = b"\xaa" * 32
    entry = s.put(
        key=key,
        function_name="mod.foo",
        serializer="msgpack",
        payload=b"hello",
        file_dependencies=["data.csv"],
        code_dependencies=["abc"],
        run_duration_ns=12345,
    )
    assert entry.key == key
    assert entry.size_bytes == 5
    assert s.get_payload(key) == b"hello"
    fetched = s.get_entry(key)
    assert fetched is not None
    assert fetched.function_name == "mod.foo"
    assert fetched.file_dependencies == ["data.csv"]


def test_get_missing_returns_none(tmp_path):
    s = Store(tmp_path / "c")
    assert s.get_entry(b"\x00" * 32) is None
    assert s.get_payload(b"\x00" * 32) is None


def test_hit_increments_counter(tmp_path):
    s = Store(tmp_path / "c")
    key = b"\xbb" * 32
    s.put(key=key, function_name="f", serializer="msgpack", payload=b"x")
    s.hit(key)
    s.hit(key)
    e = s.get_entry(key)
    assert e is not None and e.hits == 2


def test_delete(tmp_path):
    s = Store(tmp_path / "c")
    key = b"\xcc" * 32
    s.put(key=key, function_name="f", serializer="msgpack", payload=b"x")
    assert s.delete(key) is True
    assert s.get_entry(key) is None
    assert s.get_payload(key) is None
    assert s.delete(key) is False


def test_delete_by_function_name(tmp_path):
    s = Store(tmp_path / "c")
    s.put(key=b"\xa1" * 32, function_name="f", serializer="msgpack", payload=b"1")
    s.put(key=b"\xa2" * 32, function_name="f", serializer="msgpack", payload=b"2")
    s.put(key=b"\xa3" * 32, function_name="g", serializer="msgpack", payload=b"3")
    n = s.delete_function("f")
    assert n == 2
    assert len(s.all_entries()) == 1


def test_clear(tmp_path):
    s = Store(tmp_path / "c")
    for i in range(5):
        k = bytes([i]) * 32
        s.put(key=k, function_name=f"f{i}", serializer="msgpack", payload=b"x")
    n = s.clear()
    assert n == 5
    assert len(s.all_entries()) == 0


def test_stats(tmp_path):
    s = Store(tmp_path / "c")
    s.put(
        key=b"\xa1" * 32, function_name="f", serializer="msgpack",
        payload=b"x" * 100, run_duration_ns=1_000_000_000,
    )
    s.hit(b"\xa1" * 32)
    st = s.stats()
    assert st["entries"] == 1
    assert st["total_bytes"] == 100
    assert st["total_hits"] == 1
    assert st["estimated_ns_saved"] == 1_000_000_000


def test_atomic_write_no_partials(tmp_path, monkeypatch):
    """If the write fails mid-flight, no partial blob is left around."""
    s = Store(tmp_path / "c")
    key = b"\xd0" * 32
    # Patch os.replace to raise, simulating a crash between flush and rename.
    import os as _os

    original_replace = _os.replace
    calls = {"n": 0}

    def boom(src, dst):
        calls["n"] += 1
        raise OSError("simulated")

    monkeypatch.setattr(_os, "replace", boom)
    import pytest

    with pytest.raises(OSError):
        s.put(key=key, function_name="f", serializer="msgpack", payload=b"x")
    monkeypatch.setattr(_os, "replace", original_replace)
    # Cache dir is clean — no leftover .tmp files.
    leftovers = list((tmp_path / "c" / "blobs").rglob(".tmp.*"))
    assert leftovers == []
    # And the final path was never created.
    assert s.get_payload(key) is None
