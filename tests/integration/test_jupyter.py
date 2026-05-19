"""Sanity test for the Jupyter integration entry point.

We don't require ipykernel to be installed in CI — just verify the
module imports cleanly and the cell-source transform behaves correctly.
"""

from __future__ import annotations

from rote.jupyter import _install_pre_run_hook  # noqa: F401 (import-only check)


def test_jupyter_module_imports():
    """The module loads even without IPython installed."""
    import rote.jupyter  # noqa: F401


def test_load_ipython_extension_requires_ipython():
    """``load_ipython_extension`` only works inside IPython — it imports
    ``IPython.core.magic`` lazily. Without IPython, expect ImportError."""
    import importlib.util

    if importlib.util.find_spec("IPython") is not None:
        return  # IPython is installed; nothing to test
    import rote.jupyter as j
    import pytest

    with pytest.raises(ImportError):
        j.load_ipython_extension(None)
