"""Auto-mode AST transform.

When the user runs ``rote run script.py``, we parse the script's AST and
wrap every top-level function definition with ``@rote.cache`` *before*
executing it. This is the only way to actually make the no-decorator
promise hold: by the time CPython's interpreter sees the function call,
it must already be wrapped.

We are deliberately conservative:

* Only ``def`` statements at module top-level are wrapped (not nested
  defs, not class methods — those have surprising semantics around
  ``self`` identity that complicates fingerprinting).
* If the user already decorated a function, we leave it alone.
* Class methods are skipped — instance mutation makes naive caching wrong.

The transform inserts ``import rote`` if it isn't already imported.
"""

from __future__ import annotations

import hashlib
import json
import os

import libcst as cst


class _AutoCacheTransformer(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self._depth: int = 0  # nesting depth — only wrap at depth 0
        self._in_class: int = 0
        self.wrapped: list[str] = []
        self.has_rote_import: bool = False

    # Track nesting

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._depth += 1

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._in_class += 1
        self._depth += 1

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self._in_class -= 1
        self._depth -= 1
        return updated_node

    # Detect existing imports

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            name = alias.name
            if isinstance(name, cst.Name) and name.value == "rote":
                self.has_rote_import = True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if isinstance(node.module, cst.Name) and node.module.value == "rote":
            self.has_rote_import = True

    # Wrap top-level functions

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._depth -= 1
        # Only wrap top-level (depth-1 frame is module) outside of classes.
        # After decrement, depth==0 means this function was at module top-level.
        if self._depth != 0 or self._in_class > 0:
            return updated_node
        # Skip already-decorated-with-rote-anything to avoid double-wrap.
        for dec in updated_node.decorators:
            text = _decorator_text(dec)
            if "rote" in text:
                return updated_node
        # Skip dunder functions and obvious special cases.
        if updated_node.name.value.startswith("__") and updated_node.name.value.endswith("__"):
            return updated_node
        # Prepend @rote.cache.
        new_dec = cst.Decorator(
            decorator=cst.Attribute(
                value=cst.Name("rote"),
                attr=cst.Name("cache"),
            )
        )
        self.wrapped.append(updated_node.name.value)
        return updated_node.with_changes(decorators=(new_dec, *updated_node.decorators))


def _decorator_text(dec: cst.Decorator) -> str:
    # Cheap rendering of the decorator expression.
    try:
        return cst.Module(body=[]).code_for_node(dec.decorator)
    except Exception:  # noqa: BLE001
        return ""


def transform(source: str) -> tuple[str, list[str]]:
    """Return (transformed_source, names_wrapped).

    The transformed source has ``import rote`` injected at the top if
    needed and every top-level ``def`` wrapped with ``@rote.cache``.
    """
    return _transform_impl(source)


def transform_file(path: str) -> tuple[str, list[str]]:
    """Like ``transform`` but memoizes on (path, mtime, size).

    The libcst parse + transform is the dominant cost of ``rote run`` on
    small scripts; mtime-keyed memoization makes repeat runs of unchanged
    scripts ~10× faster.
    """
    try:
        st = os.stat(path)
        key = (path, st.st_size, int(st.st_mtime_ns))
    except OSError:
        with open(path, encoding="utf-8") as f:
            return _transform_impl(f.read())

    cache_dir = _autowrap_cache_dir()
    if cache_dir is None:
        with open(path, encoding="utf-8") as f:
            return _transform_impl(f.read())

    key_hash = hashlib.sha256(repr(key).encode()).hexdigest()[:32]
    cache_file = os.path.join(cache_dir, f"{key_hash}.json")
    try:
        with open(cache_file, encoding="utf-8") as f:
            blob = json.load(f)
        return blob["src"], blob["wrapped"]
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError):
        pass

    with open(path, encoding="utf-8") as f:
        src = f.read()
    out_src, wrapped = _transform_impl(src)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"src": out_src, "wrapped": wrapped}, f)
    except OSError:
        pass  # transform succeeded; cache write failure is non-fatal
    return out_src, wrapped


def _autowrap_cache_dir() -> str | None:
    """Cache dir for transformed sources. Lives under the rote cache dir."""
    try:
        from .config import get_config

        return os.path.join(str(get_config().cache_dir), "autowrap")
    except Exception:
        return None


def _transform_impl(source: str) -> tuple[str, list[str]]:
    module = cst.parse_module(source)
    transformer = _AutoCacheTransformer()
    out = module.visit(transformer)
    if not isinstance(out, cst.Module):  # pragma: no cover — defensive
        return source, []
    if not transformer.has_rote_import and transformer.wrapped:
        # Insert `import rote` after the module docstring and any
        # ``from __future__ import ...`` statements (which must remain first).
        import_stmt = cst.SimpleStatementLine(
            body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("rote"))])]
        )
        body = list(out.body)
        insert_at = 0
        # Skip module docstring (a bare string expression).
        if body and isinstance(body[0], cst.SimpleStatementLine):
            stmt = body[0].body[0] if body[0].body else None
            if isinstance(stmt, cst.Expr) and isinstance(stmt.value, (cst.SimpleString, cst.ConcatenatedString)):
                insert_at = 1
        # Skip __future__ imports.
        while insert_at < len(body):
            node = body[insert_at]
            if isinstance(node, cst.SimpleStatementLine) and node.body:
                first = node.body[0]
                if (
                    isinstance(first, cst.ImportFrom)
                    and isinstance(first.module, cst.Name)
                    and first.module.value == "__future__"
                ):
                    insert_at += 1
                    continue
            break
        body.insert(insert_at, import_stmt)
        out = out.with_changes(body=tuple(body))
    return out.code, transformer.wrapped
