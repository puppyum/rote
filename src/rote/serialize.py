"""Layer 3a — Type-dispatched serialization registry.

Resolution order (first match wins):

1. ``pyarrow.Table`` → PyArrow IPC
2. ``pandas.DataFrame`` → Arrow → PyArrow IPC
3. ``polars.DataFrame`` → Arrow → PyArrow IPC
4. ``numpy.ndarray`` → numpy ``.npy``
5. ``torch.Tensor`` → ``safetensors``
6. primitives + simple containers → ``msgpack``
7. everything else → ``cloudpickle``

Every serializer registers a ``name`` (stored in the SQLite index), an
``encode`` callable and a ``decode`` callable. We also store an
``input_hash`` per serializer that is content-addressable for use as an
argument fingerprint.
"""

from __future__ import annotations

import hashlib
import io
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cloudpickle  # type: ignore[import-untyped]
import msgpack  # type: ignore[import-untyped]

try:
    import blake3 as _blake3_mod  # type: ignore[import-untyped]

    def _hash(data: bytes) -> bytes:
        return _blake3_mod.blake3(data).digest()  # type: ignore[no-any-return]

except ImportError:  # pragma: no cover

    def _hash(data: bytes) -> bytes:
        return hashlib.sha256(data).digest()


# ----------------------------------------------------- Optional dependencies

try:
    import numpy as _np  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]

try:
    import pyarrow as _pa  # type: ignore[import-untyped]
    import pyarrow.ipc as _pa_ipc  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _pa = None  # type: ignore[assignment]
    _pa_ipc = None  # type: ignore[assignment]

try:
    import pandas as _pd  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _pd = None  # type: ignore[assignment]

try:
    import polars as _pl  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _pl = None  # type: ignore[assignment]

try:
    import safetensors.numpy as _safetensors_np  # type: ignore[import-untyped]
    import safetensors.torch as _safetensors_torch  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _safetensors_np = None  # type: ignore[assignment]
    _safetensors_torch = None  # type: ignore[assignment]

try:
    import torch as _torch  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _torch = None  # type: ignore[assignment]


# ----------------------------------------------------------------- Registry


@dataclass
class Serializer:
    """A single (name, predicate, encode, decode) triple."""

    name: str
    predicate: Callable[[Any], bool]
    encode: Callable[[Any], bytes]
    decode: Callable[[bytes], Any]


_REGISTRY: list[Serializer] = []
_BY_NAME: dict[str, Serializer] = {}


def register(s: Serializer) -> None:
    _REGISTRY.append(s)
    _BY_NAME[s.name] = s


def get_by_name(name: str) -> Serializer:
    return _BY_NAME[name]


# Cache of (type → serializer) for types whose predicate is purely structural
# (depends only on the type, not on element contents). Containers
# (list/tuple/dict) are excluded because their msgpackability depends on
# what's inside them.
_PICK_CACHE: dict[type, Serializer] = {}
# int is excluded — its msgpackability depends on the value (64-bit range).
_CACHEABLE_TYPES: set[type] = {str, bytes, bytearray, float, bool, type(None)}
if _np is not None:
    _CACHEABLE_TYPES.add(_np.ndarray)
if _pa is not None:
    _CACHEABLE_TYPES.add(_pa.Table)
if _torch is not None:
    _CACHEABLE_TYPES.add(_torch.Tensor)


def pick(value: Any) -> Serializer:
    t = type(value)
    cached = _PICK_CACHE.get(t)
    if cached is not None:
        return cached
    for s in _REGISTRY:
        try:
            if s.predicate(value):
                if t in _CACHEABLE_TYPES:
                    _PICK_CACHE[t] = s
                return s
        except Exception:
            continue
    raise RuntimeError("no serializer matched — cloudpickle is the universal fallback")


# --------------------------------------------------------- Built-in encoders


def _encode_arrow_table(t: Any) -> bytes:
    assert _pa_ipc is not None
    new_stream: Any = _pa_ipc.new_stream
    buf = io.BytesIO()
    with new_stream(buf, t.schema) as writer:
        writer.write_table(t)
    return buf.getvalue()


def _decode_arrow_table(data: bytes) -> Any:
    assert _pa_ipc is not None
    open_stream: Any = _pa_ipc.open_stream
    with open_stream(io.BytesIO(data)) as reader:
        return reader.read_all()


def _encode_pandas(df: Any) -> bytes:
    assert _pa is not None
    return _encode_arrow_table(_pa.Table.from_pandas(df, preserve_index=True))


def _decode_pandas(data: bytes) -> Any:
    return _decode_arrow_table(data).to_pandas()


def _encode_polars(df: Any) -> bytes:
    return _encode_arrow_table(df.to_arrow())


def _decode_polars(data: bytes) -> Any:
    assert _pl is not None
    return _pl.from_arrow(_decode_arrow_table(data))


def _encode_numpy(arr: Any) -> bytes:
    assert _np is not None
    buf = io.BytesIO()
    _np.save(buf, arr, allow_pickle=False)
    return buf.getvalue()


def _decode_numpy(data: bytes) -> Any:
    assert _np is not None
    return _np.load(io.BytesIO(data), allow_pickle=False)


def _encode_torch(t: Any) -> bytes:
    assert _safetensors_torch is not None
    return _safetensors_torch.save({"t": t})  # type: ignore[no-any-return]


def _decode_torch(data: bytes) -> Any:
    assert _safetensors_torch is not None
    return _safetensors_torch.load(data)["t"]


_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _is_msgpackable(value: Any, _seen: set[int] | None = None) -> bool:
    """Check whether a value is composed of msgpack-native primitives.

    Strict: only types msgpack can encode directly without conversion. msgpack
    tops out at 64-bit ints; bigger integers fall through to cloudpickle.
    Cyclic containers fail the check (returning False) rather than recurse
    forever.
    """
    if value is None or isinstance(value, (str, bytes, bytearray, float, bool)):
        return True
    if isinstance(value, int):
        return _INT64_MIN <= value <= _INT64_MAX
    if isinstance(value, (list, tuple, dict)):
        seen = _seen if _seen is not None else set()
        if id(value) in seen:
            return False
        seen.add(id(value))
        try:
            if isinstance(value, dict):
                for k, v in value.items():
                    if not isinstance(k, (str, bytes, int, float, bool)):
                        return False
                    if isinstance(k, int) and not (_INT64_MIN <= k <= _INT64_MAX):
                        return False
                    if not _is_msgpackable(v, seen):
                        return False
                return True
            return all(_is_msgpackable(v, seen) for v in value)
        finally:
            seen.discard(id(value))
    return False


def _encode_msgpack(value: Any) -> bytes:
    # use_bin_type keeps str/bytes distinct on roundtrip
    return msgpack.packb(value, use_bin_type=True)  # type: ignore[no-any-return]


def _decode_msgpack(data: bytes) -> Any:
    return msgpack.unpackb(data, raw=False)


def _encode_cloudpickle(value: Any) -> bytes:
    return cloudpickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)  # type: ignore[no-any-return]


def _decode_cloudpickle(data: bytes) -> Any:
    return cloudpickle.loads(data)


# Registration in order of specificity.

if _pa is not None:
    register(
        Serializer(
            name="arrow",
            predicate=lambda v: isinstance(v, _pa.Table),
            encode=_encode_arrow_table,
            decode=_decode_arrow_table,
        )
    )
    if _pd is not None:
        register(
            Serializer(
                name="pandas",
                predicate=lambda v: isinstance(v, _pd.DataFrame),
                encode=_encode_pandas,
                decode=_decode_pandas,
            )
        )
    if _pl is not None:
        register(
            Serializer(
                name="polars",
                predicate=lambda v: isinstance(v, _pl.DataFrame),
                encode=_encode_polars,
                decode=_decode_polars,
            )
        )

if _np is not None:
    register(
        Serializer(
            name="numpy",
            predicate=lambda v: isinstance(v, _np.ndarray),
            encode=_encode_numpy,
            decode=_decode_numpy,
        )
    )

if _torch is not None and _safetensors_torch is not None:
    register(
        Serializer(
            name="torch-safetensors",
            predicate=lambda v: isinstance(v, _torch.Tensor),
            encode=_encode_torch,
            decode=_decode_torch,
        )
    )

register(
    Serializer(
        name="msgpack",
        predicate=_is_msgpackable,
        encode=_encode_msgpack,
        decode=_decode_msgpack,
    )
)

register(
    Serializer(
        name="cloudpickle",
        predicate=lambda v: True,  # universal fallback
        encode=_encode_cloudpickle,
        decode=_decode_cloudpickle,
    )
)


# ------------------------------------------------------------- Public helpers


def encode(value: Any) -> tuple[str, bytes]:
    """Encode ``value`` to bytes. Returns ``(serializer_name, bytes)``."""
    s = pick(value)
    return s.name, s.encode(value)


def decode(name: str, data: bytes) -> Any:
    """Decode bytes previously produced by :func:`encode`."""
    return get_by_name(name).decode(data)


def fingerprint(value: Any) -> bytes:
    """A stable 32-byte content fingerprint of ``value``.

    Used both for cache-key composition (argument identity) and for purity's
    copy-on-call mutation check. If the value cannot be serialized at all
    (e.g. live generators, open file handles, threading locks), we emit a
    unique fingerprint based on ``id`` — guaranteed cache miss, never crash.
    """
    try:
        name, data = encode(value)
        return _hash(name.encode() + b"\0" + data)
    except Exception:
        return _hash(b"unserializable\0" + repr(type(value)).encode() + str(id(value)).encode())


def serializer_names() -> list[str]:
    return [s.name for s in _REGISTRY]
