"""``rote`` command-line interface.

Subcommands:

    rote run script.py [args...]   — run a Python script with auto() active
    rote status                    — print stats for the cache in the CWD
    rote clear                     — wipe the cache in the CWD
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import auto, clear, configure, stats
from .autowrap import transform_file


def _cmd_run(args: argparse.Namespace) -> int:
    script = Path(args.script).resolve()
    if not script.exists():
        print(f"rote: script not found: {script}", file=sys.stderr)
        return 2
    configure(cache_dir=Path(args.cache_dir), verbose=args.verbose)
    sys.argv = [str(script), *args.script_args]
    sys.path.insert(0, str(script.parent))
    # Install the import hook so any user-code module the script imports
    # also gets its top-level functions wrapped. Library code (stdlib,
    # site-packages) is untouched.
    if not args.no_import_hook:
        from .import_hook import install as install_hook

        install_hook()
    transformed, wrapped = transform_file(str(script))
    if args.verbose:
        print(f"rote: wrapped {len(wrapped)} top-level functions: {wrapped}", file=sys.stderr)
    code = compile(transformed, str(script), "exec")
    namespace: dict[str, object] = {
        "__name__": "__main__",
        "__file__": str(script),
        "__builtins__": __builtins__,
    }
    with auto():
        exec(code, namespace)
    if args.verbose:
        print(json.dumps(stats(), indent=2), file=sys.stderr)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    configure(cache_dir=Path(args.cache_dir))
    print(json.dumps(stats(), indent=2))
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    configure(cache_dir=Path(args.cache_dir))
    n = clear()
    print(f"removed {n} entries")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rote", description="Automatic memoization for Python.")
    p.add_argument(
        "--cache-dir",
        default=".rote",
        help="Cache directory (default: .rote)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a script with auto-memoization enabled.")
    run.add_argument("script")
    run.add_argument("script_args", nargs=argparse.REMAINDER)
    run.add_argument("-v", "--verbose", action="store_true")
    run.add_argument(
        "--no-import-hook",
        action="store_true",
        help="Don't AST-wrap imported user-code modules. Only the entry script is wrapped.",
    )
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="Print cache statistics.")
    status.set_defaults(func=_cmd_status)

    clear_ = sub.add_parser("clear", help="Remove all cached entries.")
    clear_.set_defaults(func=_cmd_clear)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
