# rote

**Automatic, dependency-aware memoization for Python — without forking the interpreter.**

`rote` is a modern, pure-Python re-implementation of [IncPy (Guo & Engler,
ISSTA 2011)][1]. It does what the original did — observe your program at runtime,
identify pure long-running function calls, and persist their results across
runs — but on contemporary Python (≥3.12), without an interpreter fork, with
sharper invalidation, faster serialization, and a real testing protocol.

[1]: https://pgbovine.net/projects/pubs/guo-IncPy-ISSTA-2011.pdf

## 30-second pitch

You're a researcher. You change one line in `analyze.py`, save, re-run. Plain
Python re-does the 90 seconds of feature extraction, the 30 seconds of model
training, and the 2 seconds of plotting — all to see one tweaked plot. That
re-work is the problem IncPy named in 2011 and it is still the problem in 2026.

```bash
pip install rote
rote run analyze.py
```

That's the whole setup. No decorators. No imports inside your script. Just
prefix your script invocation with `rote run` and the second time you change
the plot, only the plot re-runs.

## What gets cached

A function call is memoized when *all* of the following are true:

1. The function ran for at least `min_duration_s` (default **1 s**). Tiny
   functions are skipped — the overhead would dwarf the benefit.
2. The function did not perform impure I/O. **Network**, **subprocess**,
   **file append**, **exec/eval**, and the usual stdlib non-determinism sources
   (`time.time()`, `random.random()`, `os.environ`, `uuid.uuid4()`, …) all
   mark a call impure.
3. No argument was mutated during the call (verified by hashing inputs at
   entry and exit).
4. The function's source code, every function it transitively calls, and
   every file it read have not changed since the cached call.

If any of those are violated, `rote` re-runs the function rather than risk a
stale result. **Stale results are a P0 bug.** We have 36 perturbation tests
(`tests/correctness/test_perturbation.py`) and 42 differential tests
(`tests/correctness/test_differential.py`) that enforce zero false negatives.

## Quickstart

```bash
git clone https://github.com/puppyum/rote
cd rote
uv venv --python 3.13 && source .venv/bin/activate
uv pip install -e ".[dev,all]"
```

Then either:

```bash
# Automatic mode — zero changes to your script.
rote run examples/e4_numpy_pipeline.py
# (Run a second time to feel the speedup.)
```

Or with the explicit escape-hatch decorator:

```python
import rote

@rote.cache
def build_features(df):
    ...
```

Inside a notebook or REPL session:

```python
import rote
with rote.auto():
    # every call inside this block is a memoization candidate
    result = my_pipeline(data)
```

## Public API

| Name | Purpose |
|---|---|
| `rote.cache` | Explicit decorator. Escape hatch for known-good functions. |
| `rote.auto()` | Context manager. Inside, the tracer is live and every pure call ≥ threshold is cached. |
| `rote.invalidate(target=None)` | Drop cached entries. `target` can be a function, a qualname string, or `None` to wipe everything. |
| `rote.graph()` | `networkx.DiGraph` of observed caller → callee edges. |
| `rote.stats()` | Hits, misses, time saved (s), invalidation reasons. |
| `rote.configure(**kwargs)` | Override defaults — cache dir, `min_duration_s`, telemetry on/off, etc. |
| CLI: `rote run script.py` | Run a script under auto-mode. |
| CLI: `rote status` | Print stats for the cache in CWD. |
| CLI: `rote clear` | Wipe the cache in CWD. |

## What's better than the 2011 original

See [`docs/WHATS_NEW.md`](docs/WHATS_NEW.md) for the full deliverable — it
covers usage, what rote improves over IncPy 2011, and how researchers
benefit. The headline differences:

* **No interpreter fork.** Pure Python, runs on stock CPython 3.12+. The
  original was a 2.6.3 patch — unmaintainable today.
* **Type-dispatched serialization.** PyArrow for DataFrames, `numpy.save` for
  arrays, `safetensors` for Torch tensors, msgpack for primitives,
  cloudpickle as a last resort. The original pickled everything.
* **Sharper invalidation.** AST canonicalization means cosmetic edits
  (comments, formatting, type hints, renames) don't bust the cache. The
  original used coarse source hashes.
* **Auditable correctness harness.** 313 tests, including 96 differential and
  perturbation tests across a 30-script corpus with zero stale results.
* **Concurrency-safe.** SQLite WAL + atomic blob writes mean many processes
  can share one cache directory without corruption (tested with a 16-process
  hammer suite).

## Project layout

```
src/rote/
    __init__.py        # public API surface
    config.py          # runtime configuration
    trace.py           # Layer 1 — sys.monitoring + audit hooks
    identity.py        # Layer 2 — canonical AST + blake3
    serialize.py       # Layer 3a — type-dispatched serializers
    store.py           # Layer 3b — SQLite + blob filesystem
    purity.py          # Layer 4 — purity / mutation detection
    _impure_stdlib.py  # curated impure-symbol list
    session.py         # Layer 5 — wires it all together; public API
    cli.py             # `rote run/status/clear`

tests/
    unit/              # per-layer unit tests
    property/          # hypothesis-driven invariance + sensitivity
    integration/       # examples run end-to-end
    correctness/       # differential + perturbation + concurrency

corpus/                # 30 scripts for the correctness harness
examples/              # demo scripts
bench/                 # workload + serializer microbenchmarks
docs/                  # architecture, evaluation, what's new
```

## License

Apache-2.0. See [LICENSE](LICENSE).

## Citing IncPy

If you use rote in academic work, please cite the original IncPy paper:

```
Guo, P. J., & Engler, D. (2011). Using automatic persistent memoization to
facilitate data analysis scripting. Proceedings of the 2011 International
Symposium on Software Testing and Analysis (ISSTA '11), 287–297.
```

And the rote repository:

```
rote: a modern pure-Python reimplementation of IncPy. 2026.
https://github.com/puppyum/rote
```
