"""Layer 2 — Identity.

Function identity = ``blake3(canonical_AST(source) || canonical_ASTs(transitive_deps))``.

The canonicalizer is built on ``libcst``. We strip:

* All comments and whitespace
* All docstrings
* All pure annotation expressions (``x: int = 1`` becomes ``x = 1`` for hashing)
* All variable *names*, replaced by De Bruijn-style indices (``v0``, ``v1``, ...)

We do **not** strip:

* Literal values (a different literal MUST produce a different hash)
* Call targets (renaming ``foo`` to ``bar`` MUST change the hash)
* Control-flow shape
* Default argument expressions

Test suite (Phase 2) drives the equivalence classes — see ``tests/property/``.
"""

from __future__ import annotations

import hashlib
import inspect
import linecache
import textwrap
from collections.abc import Callable
from types import FunctionType, MethodType, ModuleType
from typing import Any

import libcst as cst

try:
    import blake3 as _blake3_mod  # type: ignore[import-untyped]

    def _hash(data: bytes) -> bytes:
        return _blake3_mod.blake3(data).digest()  # type: ignore[no-any-return]

except ImportError:  # pragma: no cover — blake3 listed as required dep

    def _hash(data: bytes) -> bytes:
        # Fallback to SHA-256 if blake3 unavailable. Only used in degraded envs.
        return hashlib.sha256(data).digest()


# Bytes prefix that goes into every key. Bumping this version invalidates
# every cached value in the cache directory without a migration.
KEY_VERSION = b"rote.v1"


# --------------------------------------------------------------- Canonicalizer


class _Renamer(cst.CSTTransformer):
    """Replace identifier names with De Bruijn-style indices.

    Two functions that differ only by consistent variable renaming receive the
    same canonical text. Keywords, attribute names, and module-level imports
    are left untouched (they carry semantics).
    """

    def __init__(self) -> None:
        super().__init__()
        # Stack of dicts: outer scope, inner scope, ... Each dict maps
        # original name → canonical name within that scope.
        self._scopes: list[dict[str, str]] = [{}]
        self._counter: int = 0

    def _new_name(self) -> str:
        n = f"_v{self._counter}"
        self._counter += 1
        return n

    def _lookup(self, name: str) -> str | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    def _declare(self, name: str) -> str:
        canon = self._new_name()
        self._scopes[-1][name] = canon
        return canon

    # --- scope management

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._scopes.append({})
        for param in node.params.params:
            self._declare(param.name.value)
        for param in node.params.kwonly_params:
            self._declare(param.name.value)
        for param in node.params.posonly_params:
            self._declare(param.name.value)
        if node.params.star_arg and isinstance(node.params.star_arg, cst.Param):
            self._declare(node.params.star_arg.name.value)
        if node.params.star_kwarg:
            self._declare(node.params.star_kwarg.name.value)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._scopes.pop()
        # Replace the function NAME with a fixed placeholder so the canonical
        # form is unaffected by the name we gave the def. Internal references
        # to the function (recursive calls) live in this scope and were
        # canonicalized already.
        return updated_node.with_changes(name=cst.Name(value="_func"))

    def visit_Lambda(self, node: cst.Lambda) -> None:
        self._scopes.append({})
        for param in node.params.params:
            self._declare(param.name.value)
        if node.params.star_arg and isinstance(node.params.star_arg, cst.Param):
            self._declare(node.params.star_arg.name.value)
        if node.params.star_kwarg:
            self._declare(node.params.star_kwarg.name.value)

    def leave_Lambda(
        self, original_node: cst.Lambda, updated_node: cst.Lambda
    ) -> cst.Lambda:
        self._scopes.pop()
        return updated_node

    def visit_CompFor(self, node: cst.CompFor) -> None:
        # Comprehensions have their own scope in Python 3.
        self._scopes.append({})

    def leave_CompFor(
        self, original_node: cst.CompFor, updated_node: cst.CompFor
    ) -> cst.CompFor:
        # We pop after the whole comprehension, but CompFor leave fires per-for.
        # For simplicity we leave the scope alive; the next comprehension opens
        # a fresh one. This means comprehension-local names don't collide with
        # outer-scope names.
        return updated_node

    # --- assignments declare names

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.Assign:
        return updated_node

    # --- the actual renaming

    def visit_Assign(self, node: cst.Assign) -> None:
        # Declare every simple-name target *before* descending — so the RHS
        # can reference the new name (uncommon but legal in walrus etc.).
        for target in node.targets:
            self._declare_targets(target.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        self._declare_targets(node.target)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        self._declare_targets(node.target)

    def visit_For(self, node: cst.For) -> None:
        self._declare_targets(node.target)

    def visit_With(self, node: cst.With) -> None:
        for item in node.items:
            if item.asname is not None:
                self._declare_targets(item.asname.name)

    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> None:
        if node.name is not None:
            target = node.name.name
            if isinstance(target, cst.Name):
                self._declare(target.value)

    def _declare_targets(self, target: cst.BaseAssignTargetExpression) -> None:
        if isinstance(target, cst.Name):
            if self._lookup(target.value) is None:
                self._declare(target.value)
        elif isinstance(target, (cst.Tuple, cst.List)):
            for el in target.elements:
                if isinstance(el, cst.Element) and isinstance(el.value, (cst.Name, cst.Tuple, cst.List)):
                    self._declare_targets(el.value)  # type: ignore[arg-type]

    def leave_Name(
        self, original_node: cst.Name, updated_node: cst.Name
    ) -> cst.BaseExpression:
        # Only rename names that were *bound* in some enclosing scope.
        # Free variables (calls into module-level names, builtins, etc.) keep
        # their identifier so renames are detectable.
        canon = self._lookup(original_node.value)
        if canon is not None:
            return cst.Name(value=canon)
        return updated_node

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.Attribute:
        # Attribute *names* stay as-is. Only the value (the object) is renamed
        # by leave_Name above.
        return updated_node


class _AnnotationStripper(cst.CSTTransformer):
    """Remove pure type annotations and docstrings — they don't affect runtime.

    Comments are stripped textually after re-emission by ``_strip_comments``.
    """

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine:
        return updated_node.with_changes(comment=None)

    def leave_TrailingWhitespace(
        self, original_node: cst.TrailingWhitespace, updated_node: cst.TrailingWhitespace
    ) -> cst.TrailingWhitespace:
        return updated_node.with_changes(comment=None)

    def leave_Param(self, original_node: cst.Param, updated_node: cst.Param) -> cst.Param:
        return updated_node.with_changes(annotation=None)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        updated = updated_node.with_changes(returns=None)
        # Strip a leading docstring (Expr → SimpleString) if present.
        body = updated.body
        if isinstance(body, cst.IndentedBlock) and body.body:
            first = body.body[0]
            if (
                isinstance(first, cst.SimpleStatementLine)
                and len(first.body) == 1
                and isinstance(first.body[0], cst.Expr)
                and isinstance(first.body[0].value, (cst.SimpleString, cst.ConcatenatedString))
            ):
                new_body = body.with_changes(body=body.body[1:] or [cst.SimpleStatementLine(body=[cst.Pass()])])
                updated = updated.with_changes(body=new_body)
        return updated

    def leave_AnnAssign(
        self,
        original_node: cst.AnnAssign,
        updated_node: cst.AnnAssign,
    ) -> cst.BaseSmallStatement | cst.RemovalSentinel:
        # ``x: int = 1`` → ``x = 1``. ``x: int`` (no value) → drop entirely.
        if updated_node.value is None:
            return cst.RemovalSentinel.REMOVE
        return cst.Assign(
            targets=[cst.AssignTarget(target=updated_node.target)],
            value=updated_node.value,
        )


_PY_KEYWORDS = frozenset(
    ["False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield", "match", "case"]
)
def _builtins_names() -> frozenset[str]:
    b: Any = __builtins__
    if isinstance(b, dict):
        return frozenset(b.keys())
    return frozenset(dir(b))


_PY_BUILTINS = _builtins_names()


def _is_builtin(name: str) -> bool:
    return name in _PY_KEYWORDS or name in _PY_BUILTINS


# ----------------------------------------------------------- Public functions


def canonical_source(source: str) -> str:
    """Return a canonical textual form of a Python source snippet.

    Two snippets that differ only by formatting, comments, docstrings, type
    hints, or consistent renaming of locals produce equal canonical strings.
    """
    source = textwrap.dedent(source)
    module = cst.parse_module(source)
    stripped = module.visit(_AnnotationStripper())
    if isinstance(stripped, cst.RemovalSentinel):  # pragma: no cover — defensive
        return ""
    renamed = stripped.visit(_Renamer())
    if isinstance(renamed, cst.RemovalSentinel):  # pragma: no cover — defensive
        return ""
    # Re-emit, strip any residual comments at the textual level, and collapse
    # whitespace to a canonical form: one statement per line, no trailing
    # whitespace, no blank lines.
    code = _strip_comments(renamed.code)
    lines = [ln.rstrip() for ln in code.splitlines() if ln.strip()]
    return "\n".join(lines)


def _strip_comments(src: str) -> str:
    """Remove ``# comment`` tails from each line while preserving string literals."""
    import io
    import tokenize

    out: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return src
    last_end = (1, 0)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.NL or tok.type == tokenize.NEWLINE:
            out.append("\n")
            last_end = tok.end
            continue
        if tok.type in (tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        # Pad with single space if previous token was on the same line.
        if tok.start[0] == last_end[0] and tok.start[1] > last_end[1]:
            out.append(" ")
        elif tok.start[0] > last_end[0]:
            # New indented line — preserve indentation by emitting spaces.
            out.append(" " * tok.start[1])
        out.append(tok.string)
        last_end = tok.end
    return "".join(out)


def function_source(func: Callable[..., Any]) -> str:
    """Best-effort source extraction. Falls back to bytecode hex if source is unavailable."""
    try:
        src = inspect.getsource(func)
        return src
    except (OSError, TypeError):
        # No source — use code object bytes.
        code = getattr(func, "__code__", None)
        if code is None and isinstance(func, MethodType):
            code = func.__func__.__code__
        if code is None:
            return repr(func)
        return f"<bytecode>{code.co_code.hex()}<consts>{tuple(code.co_consts)!r}"


def function_id(func: Callable[..., Any]) -> bytes:
    """Return a 32-byte blake3 digest identifying the function's source.

    Memoized on the function object's ``__code__`` identity so repeat calls in
    a hot loop are cheap.
    """
    code = getattr(func, "__code__", None)
    if code is not None:
        return _function_id_for_code(code)
    # Fallback for builtins, partials, lambdas without code.
    return _hash(function_source(func).encode("utf-8"))


# Code objects are hashable and long-lived, but the lru_cache's strong refs
# would keep dynamically-generated code (e.g., from ``exec`` in test loops)
# alive forever. We use a plain dict + a per-code finalize callback so the
# entry is dropped when the code is GC'd. id(code) reuse is no longer a
# soundness hole because the dict key is the code object itself.
_FID_CACHE: dict[Any, bytes] = {}


def _function_id_for_code(code: Any) -> bytes:
    """Identity for a code object.

    Preference order:
      1. Real source from the file (or live linecache) → canonical AST hash.
      2. Bytecode + constants + names (covers ``exec``-defined functions).
    """
    cached = _FID_CACHE.get(code)
    if cached is not None:
        return cached
    src = linecache.getlines(code.co_filename) if code.co_filename else None
    fid: bytes | None = None
    if src:
        try:
            import inspect as _inspect

            raw = _inspect.getsource(code)
            canonical = canonical_source(raw)
            fid = _hash(canonical.encode("utf-8"))
        except (OSError, TypeError, cst.ParserSyntaxError):
            fid = None
    if fid is None:
        parts = [
            code.co_code,
            repr(tuple(code.co_consts)).encode("utf-8"),
            repr(tuple(code.co_names)).encode("utf-8"),
            repr(tuple(code.co_varnames)).encode("utf-8"),
        ]
        fid = _hash(b"\0".join(parts))
    _FID_CACHE[code] = fid
    # Drop the cached entry when the code object is GC'd.
    import contextlib
    import weakref as _weakref

    with contextlib.suppress(TypeError):
        # Some code objects refuse weakref; the cache entry leaks until process exit.
        _weakref.finalize(code, _FID_CACHE.pop, code, None)
    return fid


def composite_id(*parts: bytes) -> bytes:
    """Combine any number of identity components into one digest."""
    h = _blake3_mod.blake3() if "_blake3_mod" in globals() else hashlib.sha256()
    h.update(KEY_VERSION)
    for p in parts:
        h.update(len(p).to_bytes(4, "big"))
        h.update(p)
    return h.digest() if hasattr(h, "digest") else h.digest()  # type: ignore[return-value]


def cache_key(
    func_id: bytes,
    input_id: bytes,
    file_dep_ids: bytes = b"",
    global_dep_ids: bytes = b"",
) -> bytes:
    """Compose the four identity pieces into the final 32-byte cache key."""
    return composite_id(func_id, input_id, file_dep_ids, global_dep_ids)


def hexkey(key: bytes) -> str:
    """Human-readable hex form. Used in logs and tests only."""
    return key.hex()


def _unwrap(func: Any) -> Any:
    """Walk ``__wrapped__`` chain to get the original function."""
    seen: set[int] = set()
    while hasattr(func, "__wrapped__") and id(func) not in seen:
        seen.add(id(func))
        func = func.__wrapped__
    return func


def transitive_function_ids(func: Callable[..., Any], _seen: set[int] | None = None) -> bytes:
    """Hash of the function and every function it transitively references.

    Three sources of callees:
      * Module globals — names in ``code.co_names`` looked up in ``__globals__``.
      * Closure cells — names in ``code.co_freevars`` paired with ``__closure__``.
      * Default-arg values — items in ``__defaults__`` and ``__kwdefaults__``.

    Each callee is unwrapped (functools.wraps / our @cache) before hashing so
    we capture the *original* source, not the wrapper. Recursive references
    are deduplicated via the seen-set.
    """
    _seen = _seen if _seen is not None else set()
    func = _unwrap(func)
    fid = function_id(func)
    code = getattr(func, "__code__", None)
    if code is None or id(code) in _seen:
        return fid
    _seen.add(id(code))
    parts: list[bytes] = [fid]

    # Module globals
    g = getattr(func, "__globals__", {}) or {}
    for name in code.co_names:
        target = g.get(name)
        if target is not None:
            unwrapped = _unwrap(target)
            if isinstance(unwrapped, (FunctionType, MethodType)):
                parts.append(transitive_function_ids(unwrapped, _seen))

    # Closure cells (free variables)
    closure = getattr(func, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                cell_val = cell.cell_contents
            except ValueError:
                continue
            unwrapped = _unwrap(cell_val)
            if isinstance(unwrapped, (FunctionType, MethodType)):
                parts.append(transitive_function_ids(unwrapped, _seen))

    # Default arguments that are themselves functions
    for default in (getattr(func, "__defaults__", None) or ()):
        unwrapped = _unwrap(default)
        if isinstance(unwrapped, (FunctionType, MethodType)):
            parts.append(transitive_function_ids(unwrapped, _seen))
    for default in (getattr(func, "__kwdefaults__", None) or {}).values():
        unwrapped = _unwrap(default)
        if isinstance(unwrapped, (FunctionType, MethodType)):
            parts.append(transitive_function_ids(unwrapped, _seen))

    return composite_id(*parts)


# --------------------------------------------------------- Module helpers


def module_source_hash(mod: ModuleType) -> bytes:
    """Hash a module's source file. Used as a coarse global-dep signal."""
    path = getattr(mod, "__file__", None)
    if not path:
        return _hash(getattr(mod, "__name__", "<anon>").encode())
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return _hash(path.encode())
    return _hash(data)
