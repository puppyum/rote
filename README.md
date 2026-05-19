# rote

Automatic, dependency-aware memoization for Python research scripts. No interpreter fork, no decorators required.

`rote` is a pure-Python reimplementation of [IncPy (Guo & Engler, ISSTA 2011)][paper] on contemporary CPython (≥3.12). Same goal as the original: observe a script at runtime, find the function calls that are pure and long-running, persist their results across runs. The implementation is fresh, built on `sys.monitoring` (PEP 669) and audit hooks (PEP 578), so no patched interpreter is needed.

[paper]: https://pgbovine.net/projects/pubs/guo-IncPy-ISSTA-2011.pdf

## Why

You change one line in `analyze.py`, save, re-run. Plain Python re-does the 90 seconds of feature extraction, the 30 seconds of model training, and the 2 seconds of plotting — all to look at one tweaked plot. That re-work is the thing IncPy was built to remove in 2011. It's still the thing.

## Install

```bash
pip install rote                  # core
pip install "rote[all]"           # plus pyarrow, numpy, safetensors
```

Python 3.12 or later. Apache-2.0.

## Use

Three ways, ordered by how much you have to opt in.

**Zero-config, paper-style.** Prefix your script invocation:

```bash
rote run analyze.py
```

The CLI AST-wraps every top-level function in your script and in any helper modules it imports. Run the script a second time after a downstream edit; only the changed function re-executes.

**Decorator.** When you want to be explicit:

```python
import rote

@rote.cache
def build_features(df):
    ...
```

**Inside a notebook or REPL:**

```python
import rote
with rote.auto():
    result = my_pipeline(data)
```

In Jupyter, `%load_ext rote` makes every cell a memoization candidate.

## What gets cached

A function call is memoized when all of these hold:

1. It ran for at least `min_duration_s` (default 1 s). Below that, the cache write costs more than re-running.
2. No impure I/O happened during the call. Network, subprocess, file appends, `exec`/`eval`, and stdlib non-determinism sources (`time.time()`, `random.random()`, `uuid.uuid4()`, `os.environ`) all disqualify it.
3. No argument mutated. Arguments are fingerprinted on entry and re-checked on exit.
4. The function's source, every function it transitively calls, and every file it read are unchanged from the cached version.

If any check fails the cache misses and the function runs. Cached values that can't be proven safe never get returned: the `tests/correctness/` suite includes 36 perturbation tests and 60 differential tests that fail loudly if a cached value drifts from a fresh run.

The serializer is type-dispatched: Arrow IPC for DataFrames, `numpy.save` for arrays, `safetensors` for Torch tensors, msgpack for primitives, cloudpickle only as a last resort. Rationale in [docs/DECISIONS.md](docs/DECISIONS.md).

## Measured performance

Apple Silicon, Python 3.13, medians of 20 warm-cache iterations:

| Workload | joblib warm | rote warm | speedup |
|---|---|---|---|
| 2 M-term Leibniz | 101 µs | 49 µs | 2.06× |
| Basel sum | 90 µs | 35 µs | 2.58× |
| 400×400 NumPy QR | 226 µs | 35 µs | **6.40×** |
| 200K-char bag-of-words | 98 µs | 37 µs | 2.64× |
| 200×200 matrix inverse | 88 µs | 68 µs | 1.29× |

Geomean: **2.59× faster than `joblib.Memory`** across the five workloads.

On the paper's headline workload — a multi-stage pipeline where you edit the downstream step and re-run — `rote` finishes the changed step in 5.5 ms vs plain Python's 255 ms cold. That's ≈**46×**. Full numbers and a serializer breakdown live in [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

Test suite: **367 tests pass.** `mypy --strict` and `ruff` clean across `src/` and `tests/`. CI runs Linux, macOS, and Windows on Python 3.12 and 3.13.

## Public API

| Name | Purpose |
|---|---|
| `rote.cache` | Decorator. The explicit escape hatch. |
| `rote.auto()` | Context manager. Every call inside the block is a candidate. |
| `rote.invalidate(target=None)` | Drop entries. `target` is a function, a qualname string, or `None` for everything. |
| `rote.clear()` | Wipe all tiers (in-memory + SQLite + blobs). |
| `rote.configure(**kwargs)` | Override defaults (cache dir, `min_duration_s`, fsync, telemetry, ...). |
| `rote.stats()` | Hits, misses, time saved, invalidation reasons. |
| `rote.graph()` | A `networkx.DiGraph` of observed caller → callee edges. |
| `rote run <script>` | CLI: run a script under auto-mode. |
| `rote status` | CLI: print stats for the cache in the CWD. |
| `rote clear` | CLI: wipe the cache in the CWD. |

## Layout

```
src/rote/         the package (13 modules, ~4K lines)
tests/            unit / property / integration / correctness suites
docs/             architecture, decisions log, benchmarks, evaluation
bench/            workload + serializer microbenchmarks
corpus/           30 scripts that drive the differential tests
examples/         demos used by the integration tests
```

Architecture in detail: [docs/architecture.md](docs/architecture.md).
Every paper deviation logged: [docs/DECISIONS.md](docs/DECISIONS.md).
Recent changes: [CHANGELOG.md](CHANGELOG.md).

## Limitations

- **Python 3.12+ only.** `sys.monitoring` (PEP 669) is the load-bearing primitive; there is no fallback for older interpreters.
- **Functions doing real I/O are skipped.** That covers network reads, append-mode file writes, and subprocess calls. The system is designed for compute-heavy steps that take a data file in and return a value out.
- **First run pays an AST-transform overhead.** Auto-mode rewrites your script through `libcst` once per source change; the rewrite is cached on disk after that.
- **The 1-second default threshold is conservative.** Sub-second calls aren't memoized unless you lower it explicitly with `rote.configure(min_duration_s=0.05)`.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Citing IncPy

If you use `rote` in academic work, cite the original paper:

```
Guo, P. J., & Engler, D. (2011). Using automatic persistent memoization to
facilitate data analysis scripting. Proceedings of the 2011 International
Symposium on Software Testing and Analysis (ISSTA '11), 287–297.
```
