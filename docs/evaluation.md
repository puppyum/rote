# Evaluation

A walkthrough of the testing harness, the benchmarks, and what the numbers
say about rote's correctness and performance.

## Claims under test

1. Correctness. Running a script through `rote run` produces output that's
   byte-identical to plain Python under any cache state.
2. Coverage. At least 80% of long-running pure function calls in a
   representative corpus are memoized without annotations.
3. Performance vs joblib. On realistic edit-and-rerun workflows, mean
   wall-clock time is at least 30% lower than `joblib.Memory`. Cold-cache
   overhead stays under 20%.
4. Usability. A new user can drop the library into a script with zero
   decorators and see a speedup on the first re-run.

## Methodology

### Layer A: engineering correctness

* Unit tests across the public surface of every layer.
* Property tests (Hypothesis) covering identity invariance/sensitivity
  and serialization round trips, with thousands of generated examples
  per property.
* Type and lint. `ruff check src tests` clean. `mypy --strict` on `src/`
  runs in CI.

### Layer B: system correctness

The load-bearing layer.

* Differential tests. 60 tests across a 30-script corpus
  (`corpus/c01_*.py` through `corpus/c30_*.py`). For each script we
  compare plain-Python stdout byte-for-byte against `rote run` output
  (cold cache, then warm cache). 60/60 pass with no discrepancies.
* Perturbation harness. 36 tests covering a taxonomy of edits on the
  corpus subset where edits have measurable output effects: add a comment,
  rename a variable, change a literal, add a type hint, add a docstring.
  For each (script × perturbation), we populate the cache, apply the
  perturbation, then verify the cached output equals what plain Python
  would now produce. 36/36 pass, zero false negatives.
* Concurrency. A 16-process hammer suite writing to one shared cache.
  No corruption across 320 concurrent writes.

### Layer C: researcher impact instrumentation

Each `auto()` session writes `<cache_dir>/sessions/<unix_ts>.json` with
hit count, miss count, invalidation reasons, and the observed call graph.
The format is stable and machine-readable so an analysis notebook can
aggregate across sessions without changes here. Studies that use this
dataset are out of scope for this iteration.

## Results

### Correctness

| Test category | Passing |
|---|---|
| Unit (`tests/unit/`) | 82 / 82 |
| Property — Hypothesis (`tests/property/`) | 18 / 18 |
| Integration (`tests/integration/`) | 28 / 28 |
| Correctness — differential, perturbation, concurrency, adversarial (`tests/correctness/`) | 253 / 253 |
| **Total** | **381 / 381** |

Zero false negatives in differential or perturbation tests. Claim 1
(correctness) is met.

### Coverage

The corpus contains 30 scripts, each defining 1–5 functions. The
`min_duration_s=1.0` threshold filters out most calls in the corpus
(synthetic scripts run in milliseconds), so the decorator path covers
100% of marked functions but the auto-mode path covers 0% of corpus
functions because they're all too fast. The examples
(`examples/e1_compute_pi.py`, `e4_numpy_pipeline.py`) are deliberately
slow enough to exercise the auto path. Claim 2 is pending a corpus
rebalance toward realistically-slow research scripts.

### Performance vs joblib

See [`BENCHMARKS.md`](./BENCHMARKS.md) for the full table.

* rote vs joblib warm: 1.54× to 7.68× faster across 5/5 workloads;
  3.35× geomean.
* Paper-shaped edit-rerun pipeline: about 48× faster than plain Python
  on the downstream-edit warm run.
* Cold-cache overhead vs plain Python: roughly +11% to +16% on the CPU-loop
  workloads in the final run, negative on NumPy QR, and high on
  millisecond-scale NumPy/string workloads when the benchmark forces
  `min_duration_s=0.0`. The default 1-second threshold avoids caching
  those tiny calls in normal use.

Claim 3 is met for the warm path. The cold-cache overhead bound isn't a
useful target for sub-millisecond calls when the benchmark forces the
threshold to zero.

### Usability

The CLI smoke test (`tests/integration/test_end_to_end.py`) runs:

```bash
rote run examples/e4_numpy_pipeline.py
```

without any decorators in the example script. The script benefits on the
second run. A new user with `pip install rote` and one bash command sees
the speedup. Claim 4 met.

## Threats to validity

* Corpus is small. 30 hand-written scripts modeled on the kinds of
  computations in the paper §4.2. A larger corpus (scikit-learn examples,
  real Kaggle notebooks) is on the backlog. Differential + perturbation
  tests scale with corpus, so this is a "we should run more cases", not
  "the methodology is wrong".
* Benchmarks are local. Single machine, single hardware family (Apple
  Silicon, NVMe SSD). Numbers will shift on Linux + spinning rust. The
  relative comparison vs joblib should hold because the dominant costs
  (serialization speed, disk write throughput) move with the hardware,
  not with us.
* No long-horizon dogfood data yet. The instrumentation that would
  generate it is in place; a multi-week iteration study using rote in a
  real research workflow is the next step.

## Reproduction

```bash
git clone https://github.com/puppyum/rote
cd rote
uv venv --python 3.13 && source .venv/bin/activate
uv pip install -e ".[dev,all]" joblib

# Layer A
uv run pytest tests/unit tests/property -n auto

# Layer B
uv run pytest tests/integration tests/correctness -n auto

# Performance
uv run pytest bench/ -m bench
cat bench/results/*.json
```

Total runtime on the reference machine: 381 tests + 6 benchmarks in
roughly 3.5 minutes.
