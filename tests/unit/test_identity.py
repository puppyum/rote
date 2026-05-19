"""Layer 2 unit tests — canonical AST + hashing."""

from __future__ import annotations

from rote.identity import (
    cache_key,
    canonical_source,
    composite_id,
    function_id,
    hexkey,
    transitive_function_ids,
)


def test_canonical_strips_comments():
    a = "def f(x):\n    # important note\n    return x + 1\n"
    b = "def f(x):\n    return x + 1\n"
    assert canonical_source(a) == canonical_source(b)


def test_canonical_strips_docstrings():
    a = 'def f(x):\n    """doc"""\n    return x + 1\n'
    b = "def f(x):\n    return x + 1\n"
    assert canonical_source(a) == canonical_source(b)


def test_canonical_strips_annotations():
    a = "def f(x: int) -> int:\n    return x + 1\n"
    b = "def f(x):\n    return x + 1\n"
    assert canonical_source(a) == canonical_source(b)


def test_canonical_consistent_rename():
    a = "def f(x):\n    y = x + 1\n    return y\n"
    b = "def f(a):\n    b = a + 1\n    return b\n"
    assert canonical_source(a) == canonical_source(b)


def test_canonical_literal_change_detected():
    a = "def f(x):\n    return x + 1\n"
    b = "def f(x):\n    return x + 2\n"
    assert canonical_source(a) != canonical_source(b)


def test_canonical_call_target_change_detected():
    a = "def f(x):\n    return foo(x)\n"
    b = "def f(x):\n    return bar(x)\n"
    assert canonical_source(a) != canonical_source(b)


def test_canonical_added_statement_detected():
    a = "def f(x):\n    return x\n"
    b = "def f(x):\n    z = 1\n    return x\n"
    assert canonical_source(a) != canonical_source(b)


def test_canonical_default_arg_change_detected():
    a = "def f(x=1):\n    return x\n"
    b = "def f(x=2):\n    return x\n"
    assert canonical_source(a) != canonical_source(b)


def test_canonical_control_flow_change_detected():
    a = "def f(x):\n    return x\n"
    b = "def f(x):\n    if x:\n        return x\n    return 0\n"
    assert canonical_source(a) != canonical_source(b)


def test_function_id_stable():
    def f(x):
        return x + 1

    a = function_id(f)
    b = function_id(f)
    assert a == b
    assert len(a) == 32


def test_function_id_different_for_different_bodies():
    def f(x):
        return x + 1

    def g(x):
        return x + 2

    assert function_id(f) != function_id(g)


def test_cache_key_composes():
    fid = b"\x00" * 32
    iid = b"\x01" * 32
    k1 = cache_key(fid, iid)
    k2 = cache_key(fid, iid)
    assert k1 == k2
    assert len(k1) == 32


def test_cache_key_input_sensitive():
    fid = b"\x00" * 32
    k1 = cache_key(fid, b"\x01" * 32)
    k2 = cache_key(fid, b"\x02" * 32)
    assert k1 != k2


def test_composite_id_order_matters():
    a = composite_id(b"x", b"y")
    b = composite_id(b"y", b"x")
    assert a != b


def test_hexkey_roundtrip():
    k = b"\xab\xcd" + b"\x00" * 30
    h = hexkey(k)
    assert h == "abcd" + "00" * 30
    assert bytes.fromhex(h) == k


def test_transitive_id_changes_when_callee_changes():
    g_v1_src = """
def g(x):
    return x + 1
def f(x):
    return g(x) * 2
"""
    g_v2_src = """
def g(x):
    return x + 99
def f(x):
    return g(x) * 2
"""
    ns1: dict = {}
    ns2: dict = {}
    exec(g_v1_src, ns1)
    exec(g_v2_src, ns2)
    a = transitive_function_ids(ns1["f"])
    b = transitive_function_ids(ns2["f"])
    assert a != b
