# rote

Modern pure-Python reimplementation of IncPy (Guo & Engler, ISSTA 2011) —
automatic, dependency-aware memoization for research scripts. No interpreter
fork, no decorators required.

**Paper:** `IncPy-memoization-in-Python-interpreter_ISSTA-2011.pdf` (read §3
+ §4 before changing purity or identity logic).

## Status

Production-ready. **313 tests pass; mypy --strict + ruff clean.**
**3.11× geomean faster than `joblib.Memory`**; **59.1× faster than plain
Python** on the paper-shaped multi-stage pipeline. Every documented
paper-fidelity gap closed (§3.3.1 write-deps, §3.3.2 perf guard, §3.4
global reachability). Supports sync/async, classmethod/staticmethod,
Jupyter, and a `sys.meta_path` import hook for cross-module auto-mode.
See [BENCHMARKS.md](BENCHMARKS.md) and [DECISIONS.md](DECISIONS.md).

## Layout

```
src/rote/
    trace.py        # L1: sys.monitoring + audit hooks
    identity.py     # L2: canonical AST + blake3 + transitive closure walk
    serialize.py    # L3a: type-dispatched (Arrow/numpy/safetensors/msgpack)
    store.py        # L3b: SQLite WAL + atomic blobs + content-hash cache
    purity.py       # L4: copy-on-call + impure stdlib + audit propagation
    _impure_stdlib.py
    session.py      # sync + async @cache + auto() + static analysis + threading
    autowrap.py     # libcst AST transform (mtime-cached) for `rote run`
    import_hook.py  # sys.meta_path finder that wraps imported user modules
    jupyter.py      # IPython %%rote cell magic + line magics + cell auto-wrap
    config.py / cli.py / __init__.py
tests/{unit,property,integration,correctness}/
bench/{test_workloads,test_paper_workload,test_serialize_microbench}.py
corpus/c01..c30   # 30 differential-test scripts
docs/architecture.md docs/WHATS_NEW.md docs/migration_from_joblib.md
```

Deeper detail: [docs/architecture.md](docs/architecture.md). Past trade-offs
+ paper deviations: [DECISIONS.md](DECISIONS.md).

## Commands

```bash
source .venv/bin/activate
rtk proxy pytest tests/ -q                    # 313 tests, ~3 min
rtk proxy pytest bench/ -m bench -q           # 6 benchmarks, ~30s
rtk proxy ruff check src                      # lint
rtk proxy mypy --strict src/                  # type check (clean)
ROTE_MIN_DURATION_S=0 python -m rote.cli run script.py  # auto-mode demo
```

`rtk proxy` is needed in front of `pytest` and `ruff` to bypass the RTK
output filter that hides their progress on this machine. Direct
`pytest`/`ruff` invocations from Codex appear to produce no output.

## Non-negotiables

- Python ≥3.12 (PEP 669 `sys.monitoring`).
- No interpreter fork. No CPython patches.
- No silent stale results. When invalidation is uncertain → cache miss.
- No raw `pickle` as default — use type-dispatched serializers.
- Apache-2.0.

## Anti-patterns (still load-bearing)

- Pickle by default → type-dispatched only.
- MD5/SHA1 → blake3 only.
- Eager invalidation "just to be safe" → defeats the purpose.
- Pretending network/seeded-random/time-of-day is pure → it isn't.
- Decorator soup → `@cache` is the escape hatch, not the primary API.
- Mocking benchmarks → use real workloads with deterministic local replacements.
- Vibe-coded mutation detection → write adversarial tests first.
- Hiding failures → write them in DECISIONS.md; no silent `# TODO`.
- Reading the original IncPy C source → it's GPL Python-2.6-era; read the
  paper, design fresh.

## Things easy to get wrong

- `test_*.py` fixtures must use `session._reset_for_testing()` AND restore
  ALL config defaults (especially `max_value_bytes`) — leaks between tests
  cause hard-to-debug bench failures. See `tests/conftest.py`.
- `_ROTE_PATH_PREFIX` uses `os.path.realpath` to resolve Homebrew/pyenv
  symlinks. Don't replace with plain `abspath` — sysconfig and `__file__`
  return different forms of the same path on macOS.
- The decorator's per-call purity monitor (TOOL_ID 5) must be paired with
  `_disable_decorator_purity_monitor()` in `_reset_for_testing` or it
  leaks across tests.
- Static bytecode analysis (`_static_impure_callees`) only catches
  `LOAD_GLOBAL` / `LOAD_DEREF` + immediate `LOAD_ATTR`. Dynamic dispatch
  (e.g. `getattr(time, 'time')()`) isn't caught — runtime PY_START is
  the safety net there.
- PY_START callbacks only fire for Python functions, not C builtins
  (`time.time`, `random.random`, `os.listdir`). The static check is what
  catches those; don't disable it without a replacement.
- The audit hook + PY_START callback BOTH filter calls originating from
  stdlib / site-packages via `_is_library_filename`. Without that filter,
  numpy's internal use of `threading.RLock` or `exec` falsely flags every
  user function calling numpy as impure.
- `input_digest = composite_id(b"arg0=" + fp(arg0), b"arg1=" + fp(arg1), ...)`
  — DO NOT replace with `sorted(arg_fps.values())`. Without the names,
  `f(1, 0)` and `f(0, 1)` collide to the same key. P0 regression risk.
- `_PERF_BLACKLIST` is a process-global set. `_reset_for_testing()` must
  clear it, or tests will see "function X was blacklisted by previous test".
- `combined_for_hash = sorted(set(file_deps) | set(write_deps))` — the
  dep_hash covers BOTH read deps and write deps so a deleted output file
  forces re-execution. Both sync + async wrappers must compute it.

## Decision protocol

Free to: implementation choices within the locked four-layer architecture;
add MIT/BSD/Apache deps; refactor freely.

Ask first: changing locked architecture; runtime dep needing compilation
beyond PyArrow/blake3; LLM-based purity inference; publishing to PyPI.

Log to DECISIONS.md: heuristics, magic constants, paper deviations,
benchmark surprises.
