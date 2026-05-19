"""Optional import hook: AST-wrap user-code modules at import time so
their top-level functions get ``@rote.cache`` automatically.

Activated by ``rote run`` (the default) so a script that does
``from helpers import process`` also benefits from caching. Modules in
the stdlib, site-packages, or our own package are left alone.
"""

from __future__ import annotations

import os.path
import sys
import sysconfig
from importlib.abc import MetaPathFinder
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_loader
from typing import Any


def _is_user_path(path: str | None) -> bool:
    """True if the path looks like the user's own source — not a library."""
    if not path:
        return False
    if path.startswith("<") and path.endswith(">"):
        return False
    real = os.path.realpath(path)
    excluded = (
        os.path.realpath(os.path.dirname(__file__)),  # ourselves
        sysconfig.get_path("stdlib") or "",
        sysconfig.get_path("platstdlib") or "",
        sysconfig.get_path("purelib") or "",
        sysconfig.get_path("platlib") or "",
        sys.prefix,
        sys.base_prefix,
    )
    for prefix in excluded:
        if prefix and (real.startswith(prefix) or real.startswith(os.path.realpath(prefix))):
            return False
    return True


class _AutoWrapLoader(SourceFileLoader):
    """SourceFileLoader subclass that runs `autowrap.transform_file`
    (mtime-cached) on the source."""

    def get_data(self, path: str) -> bytes:
        if not path.endswith(".py"):
            return super().get_data(path)
        try:
            from .autowrap import transform_file

            transformed, wrapped = transform_file(path)
            if wrapped:
                return transformed.encode("utf-8")
        except Exception:
            pass
        return super().get_data(path)


class _AutoWrapFinder(MetaPathFinder):
    """Meta-path finder that swaps the default loader for user .py modules."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        # Defer to the rest of sys.meta_path for module discovery — we just
        # rewrite the loader for source files we own. Walk the standard
        # finders to find the source path, then re-issue with our loader.
        for finder in sys.meta_path:
            if finder is self:
                continue
            try:
                spec = finder.find_spec(fullname, path, target)
            except (AttributeError, Exception):  # noqa: BLE001
                continue
            if spec is None:
                continue
            origin = spec.origin
            if not isinstance(origin, str) or not origin.endswith(".py"):
                return None  # let the normal loader handle non-.py files
            if not _is_user_path(origin):
                return None
            return spec_from_loader(
                fullname,
                _AutoWrapLoader(fullname, origin),
                origin=origin,
            )
        return None


_installed_finder: _AutoWrapFinder | None = None


def install() -> None:
    """Install the import hook. Idempotent."""
    global _installed_finder
    if _installed_finder is not None:
        return
    _installed_finder = _AutoWrapFinder()
    sys.meta_path.insert(0, _installed_finder)


def uninstall() -> None:
    """Remove the import hook (mostly for tests)."""
    global _installed_finder
    if _installed_finder is None:
        return
    import contextlib

    with contextlib.suppress(ValueError):
        sys.meta_path.remove(_installed_finder)
    _installed_finder = None
