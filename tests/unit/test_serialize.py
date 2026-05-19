"""Layer 3a unit tests — type-dispatched serialization."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from rote.serialize import decode, encode, fingerprint, pick, serializer_names


def test_registry_has_all_serializers():
    names = serializer_names()
    assert "msgpack" in names
    assert "cloudpickle" in names
    # Optionals — present in our env
    assert "numpy" in names
    assert "arrow" in names


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        12345,
        3.14,
        True,
        False,
        None,
        "hello",
        b"bytes",
        [1, 2, 3],
        (1, "two", 3.0),
        {"a": 1, "b": [2, 3]},
    ],
)
def test_msgpack_roundtrip_primitives(value):
    name, data = encode(value)
    assert name == "msgpack"
    out = decode(name, data)
    # Tuples → lists through msgpack; compare via list conversion for tuples
    if isinstance(value, tuple):
        assert list(value) == out
    else:
        assert out == value


def test_numpy_roundtrip():
    arr = np.arange(100).reshape(10, 10).astype("float32")
    name, data = encode(arr)
    assert name == "numpy"
    out = decode(name, data)
    assert np.array_equal(out, arr)


def test_arrow_roundtrip():
    t = pa.table({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    name, data = encode(t)
    assert name == "arrow"
    out = decode(name, data)
    assert out.equals(t)


def test_cloudpickle_fallback_for_complex_objects():
    class Custom:
        def __init__(self, x):
            self.x = x

        def __eq__(self, o):
            return type(o) is Custom and o.x == self.x

    name, data = encode(Custom(42))
    assert name == "cloudpickle"
    out = decode(name, data)
    assert out == Custom(42)


def test_fingerprint_stable():
    fp1 = fingerprint([1, 2, 3])
    fp2 = fingerprint([1, 2, 3])
    assert fp1 == fp2
    assert len(fp1) == 32


def test_fingerprint_sensitive():
    fp1 = fingerprint([1, 2, 3])
    fp2 = fingerprint([1, 2, 4])
    assert fp1 != fp2


def test_pick_resolution_order():
    # arrow takes priority over fallback
    assert pick(pa.table({"a": [1]})).name == "arrow"
    assert pick(np.zeros(3)).name == "numpy"
    assert pick(42).name == "msgpack"
    assert pick(object()).name == "cloudpickle"


def test_numpy_dtypes():
    for dtype in ["int8", "int64", "uint16", "float32", "float64", "complex128"]:
        arr = np.arange(20, dtype=dtype)
        name, data = encode(arr)
        out = decode(name, data)
        assert np.array_equal(out, arr) and out.dtype == arr.dtype
