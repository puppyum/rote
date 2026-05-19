"""Property tests for the store — random workloads, no corruption."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rote.store import Store


@given(
    st.lists(
        st.tuples(
            st.binary(min_size=32, max_size=32),  # key
            st.binary(max_size=1024),  # payload
        ),
        min_size=1,
        max_size=20,
        unique_by=lambda t: t[0],
    )
)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_arbitrary_put_get_consistent(tmp_path_factory, items):
    tmp = tmp_path_factory.mktemp("storeprop")
    s = Store(tmp)
    try:
        for key, payload in items:
            s.put(key=key, function_name="f", serializer="msgpack", payload=payload)
        for key, payload in items:
            assert s.get_payload(key) == payload
            entry = s.get_entry(key)
            assert entry is not None and entry.size_bytes == len(payload)
    finally:
        s.close()


@given(
    st.lists(
        st.tuples(st.binary(min_size=32, max_size=32), st.binary(max_size=128)),
        min_size=1,
        max_size=8,
        unique_by=lambda t: t[0],
    )
)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_delete_then_get_returns_none(tmp_path_factory, items):
    tmp = tmp_path_factory.mktemp("storeprop2")
    s = Store(tmp)
    try:
        for key, payload in items:
            s.put(key=key, function_name="f", serializer="msgpack", payload=payload)
        for key, _ in items:
            assert s.delete(key) is True
        for key, _ in items:
            assert s.get_payload(key) is None
            assert s.get_entry(key) is None
    finally:
        s.close()
