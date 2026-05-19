"""IPython / Jupyter integration.

Two entry points:

    %load_ext rote.jupyter        # auto-cache every cell from now on
    %rote_stats                   # print hits/misses/saved seconds
    %rote_clear                   # wipe the cache
    %%rote                        # cell magic: cache just this cell

Without ipykernel installed, importing this module is harmless — the
``load_ipython_extension`` function is only invoked by IPython itself.
"""

from __future__ import annotations

import json
from typing import Any

from . import auto, clear, configure, stats
from .autowrap import transform


def load_ipython_extension(ipython: Any) -> None:
    """Called by ``%load_ext rote.jupyter``.

    Hooks every cell execution: before run, AST-transform the cell to wrap
    its top-level functions with ``@rote.cache``; after run, telemetry
    accumulates into the session stats.
    """
    from IPython.core.magic import (  # type: ignore[import-untyped]
        Magics,
        cell_magic,
        line_magic,
        magics_class,
    )

    @magics_class
    class IncpyMagics(Magics):  # type: ignore[misc]
        @line_magic
        def rote_stats(self, line: str) -> None:  # noqa: ARG002
            print(json.dumps(stats(), indent=2))

        @line_magic
        def rote_clear(self, line: str) -> None:  # noqa: ARG002
            n = clear()
            print(f"removed {n} entries")

        @line_magic
        def rote_configure(self, line: str) -> None:
            """Parse `--key=value` pairs and forward to ``rote.configure``."""
            kwargs: dict[str, Any] = {}
            for part in line.split():
                if "=" not in part:
                    continue
                k, v = part.lstrip("-").split("=", 1)
                # Best-effort type coercion: int → float → str.
                for cast in (int, float):
                    try:
                        kwargs[k] = cast(v)
                        break
                    except ValueError:
                        continue
                else:
                    kwargs[k] = v
            configure(**kwargs)
            print(f"configured: {kwargs}")

        @cell_magic
        def rote(self, line: str, cell: str) -> Any:  # noqa: ARG002
            """Cache only THIS cell. Wraps every top-level def + runs in auto()."""
            transformed, wrapped = transform(cell)
            user_ns = self.shell.user_ns  # type: ignore[union-attr]
            with auto():
                exec(compile(transformed, "<cell>", "exec"), user_ns)
            return None

    ipython.register_magics(IncpyMagics)
    # Auto-wrap every cell from now on (the headline experience).
    _install_pre_run_hook(ipython)


def _install_pre_run_hook(ipython: Any) -> None:
    """Install a pre_run_cell event that AST-rewrites the user's code.

    The hook replaces the cell's executing source by AST-transforming the
    cell once and stashing the new compiled code on the IPython transformer
    chain. We use ``input_transformers_post`` so the user's traceback line
    numbers still point at their original source.
    """

    def transform_cell(lines: list[str]) -> list[str]:
        src = "".join(lines)
        try:
            new_src, _wrapped = transform(src)
        except Exception:
            return lines  # never break cell execution on transformer failure
        return new_src.splitlines(keepends=True)

    # Avoid double-installation across `%load_ext` re-runs.
    chain = ipython.input_transformers_post
    for existing in chain:
        if getattr(existing, "__rote_marker__", False):
            return
    transform_cell.__rote_marker__ = True  # type: ignore[attr-defined]
    chain.append(transform_cell)
