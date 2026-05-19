"""Property tests for serialize round-trips."""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays, scalar_dtypes

from rote.serialize import decode, encode


def _roundtrip(value):
    name, data = encode(value)
    return decode(name, data)


@given(st.integers())
@settings(max_examples=500)
def test_int_roundtrip(v):
    assert _roundtrip(v) == v


@given(st.floats(allow_nan=False))
@settings(max_examples=500)
def test_float_roundtrip(v):
    assert _roundtrip(v) == v


@given(st.text())
@settings(max_examples=500)
def test_text_roundtrip(v):
    assert _roundtrip(v) == v


@given(st.binary())
@settings(max_examples=500)
def test_bytes_roundtrip(v):
    assert _roundtrip(v) == v


@given(st.lists(st.integers(), max_size=50))
@settings(max_examples=500)
def test_int_list_roundtrip(v):
    assert _roundtrip(v) == v


@given(st.dictionaries(st.text(min_size=1), st.integers(), max_size=20))
@settings(max_examples=500)
def test_dict_roundtrip(v):
    assert _roundtrip(v) == v


@given(
    arrays(
        dtype=scalar_dtypes().filter(
            lambda d: d.kind in ("f", "i", "u", "b") and d.itemsize <= 8
        ),
        shape=st.tuples(st.integers(1, 8), st.integers(1, 8)),
    )
)
@settings(max_examples=200, deadline=None)
def test_numpy_roundtrip(arr):
    out = _roundtrip(arr)
    if arr.dtype.kind == "f":
        assert np.array_equal(out, arr, equal_nan=True)
    else:
        assert np.array_equal(out, arr)
    assert out.dtype == arr.dtype
