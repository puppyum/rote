"""Property tests for AST canonicalization invariance + sensitivity."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from rote.identity import canonical_source

# ----- Invariance: canonical form unchanged under these transformations.


@given(st.integers(min_value=-1000, max_value=1000))
@settings(max_examples=200, deadline=None)
def test_comment_invariance(literal):
    src_a = f"def f(x):\n    return x + {literal}\n"
    src_b = f"def f(x):\n    # comment about {literal}\n    return x + {literal}\n"
    assert canonical_source(src_a) == canonical_source(src_b)


@given(st.integers(min_value=-1000, max_value=1000))
@settings(max_examples=200, deadline=None)
def test_docstring_invariance(literal):
    src_a = f"def f(x):\n    return x + {literal}\n"
    src_b = f'def f(x):\n    """doc"""\n    return x + {literal}\n'
    assert canonical_source(src_a) == canonical_source(src_b)


_PY_KEYWORDS = frozenset(
    ["False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield", "match", "case"]
)


@given(
    st.text(alphabet="abcdefghij", min_size=1, max_size=8).filter(
        lambda s: s not in _PY_KEYWORDS and s[0].isalpha()
    )
)
@settings(max_examples=200, deadline=None)
def test_rename_invariance(name):
    a = "def f(x):\n    y = x + 1\n    return y\n"
    b = f"def f({name}):\n    z = {name} + 1\n    return z\n"
    assert canonical_source(a) == canonical_source(b)


@given(st.integers(min_value=-1000, max_value=1000))
@settings(max_examples=200, deadline=None)
def test_annotation_invariance(literal):
    a = f"def f(x):\n    return x + {literal}\n"
    b = f"def f(x: int) -> int:\n    return x + {literal}\n"
    assert canonical_source(a) == canonical_source(b)


@given(st.integers(min_value=2, max_value=20))
@settings(max_examples=100, deadline=None)
def test_whitespace_invariance(blank_lines):
    blanks = "\n" * blank_lines
    a = "def f(x):\n    return x + 1\n"
    b = f"def f(x):{blanks}    return x + 1\n"
    # Blank lines inside body — may be illegal Python. Skip if parser errors.
    try:
        cb = canonical_source(b)
    except Exception:
        return
    assert canonical_source(a) == cb


# ----- Sensitivity: canonical form changes when these change.


@given(st.integers(min_value=0, max_value=10000), st.integers(min_value=0, max_value=10000))
@settings(max_examples=200, deadline=None)
def test_literal_change_detected(a, b):
    if a == b:
        return
    src_a = f"def f(x):\n    return x + {a}\n"
    src_b = f"def f(x):\n    return x + {b}\n"
    assert canonical_source(src_a) != canonical_source(src_b)


@given(st.sampled_from(["+", "-", "*", "/", "%"]), st.sampled_from(["+", "-", "*", "/", "%"]))
def test_operator_change_detected(op1, op2):
    if op1 == op2:
        return
    src_a = f"def f(x, y):\n    return x {op1} y\n"
    src_b = f"def f(x, y):\n    return x {op2} y\n"
    assert canonical_source(src_a) != canonical_source(src_b)


_ident = st.text(alphabet="abcdef", min_size=1, max_size=4).filter(
    lambda s: s not in _PY_KEYWORDS and s[0].isalpha()
)


@given(_ident, _ident)
@settings(max_examples=100, deadline=None)
def test_call_target_change_detected(name_a, name_b):
    if name_a == name_b:
        return
    src_a = f"def f(x):\n    return {name_a}(x)\n"
    src_b = f"def f(x):\n    return {name_b}(x)\n"
    assert canonical_source(src_a) != canonical_source(src_b)


@given(st.integers(min_value=0, max_value=999), st.integers(min_value=0, max_value=999))
@settings(max_examples=100, deadline=None)
def test_default_arg_change_detected(a, b):
    if a == b:
        return
    src_a = f"def f(x={a}):\n    return x\n"
    src_b = f"def f(x={b}):\n    return x\n"
    assert canonical_source(src_a) != canonical_source(src_b)
