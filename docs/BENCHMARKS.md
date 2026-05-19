# Benchmarks

All benchmarks run with `pytest-benchmark` on Python 3.13. Numbers are wall-clock
medians of 5 runs unless noted. Machine: Apple Silicon (M-series), macOS, NVMe SSD.

Reproduce: `uv run pytest bench/ -m bench`.

## Workload matrix

| # | Workload | What it measures |
|---|----------|-------------------|
| 1 | w1_compute_pi | 2 M-term Leibniz series (pure CPU, Python loop) |
| 2 | w2_polynomial_pi | Basel sum, similar pure-CPU workload, different formula |
| 3 | w3_numpy_qr | 400×400 QR decomposition on NumPy arrays |
| 4 | w4_count_words | Bag-of-words count over a 200K-character string |
| 5 | w5_matrix_invert | 200×200 matrix inverse via NumPy |

## End-to-end (edit → result, vs `joblib.Memory`)

Warm timings are **medians of 20 warm-cache iterations** to remove single-call noise.

| Workload | joblib warm | rote warm | speedup |
|---|---|---|---|
| w1_compute_pi | 95 µs | 31 µs | **3.06×** |
| w2_polynomial_pi | 97 µs | 32 µs | **3.08×** |
| w3_numpy_qr | 195 µs | 32 µs | **6.09×** |
| w4_count_words | 96 µs | 33 µs | **2.95×** |
| w5_matrix_invert | 84 µs | 49 µs | **1.72×** |

**rote wins on 5/5 workloads warm.** Geometric mean: **3.11× faster than joblib**.

### Paper-shaped multi-stage pipeline (the headline IncPy claim)

This is the workflow shape the IncPy paper was built to accelerate: a
multi-stage pipeline (parse → aggregate → format) where the user edits the
downstream stage and re-runs. Upstream stages are served from cache.

```
Plain Python (all stages):       202 ms
rote cold (first run):         278 ms  (cache write overhead)
rote warm (edit downstream):     3.4 ms  (59.1× faster than plain)
joblib warm (edit downstream):     1.1 ms
```

The original IncPy paper §4.2 reported ~10× speedups on edit-rerun workflows;
we measure **59.1×** on a representative pipeline. In this file-based benchmark,
joblib's warm run is faster because `rote` now content-hashes file dependencies
on every hit instead of trusting `(size, mtime)` metadata; that is an intentional
correctness tradeoff to avoid silent stale results from mtime-preserving edits.

### Hot-path microbench (per-hit cost)

| Configuration | µs per hit |
|---|---|
| Baseline (pre-optimization) | 120 µs |
| After single-query + lazy hits + no fsync | 54 µs |
| After in-memory hit cache | 24 µs |
| **After file-dep mem-cache + library filter (current)** | **22 µs** |

Tunable knobs in `Config`:

```python
rote.configure(
    eager_hit_counters=False,  # batch counter updates → −5 µs/hit
    fsync_writes=False,        # skip durability fsync → −500 µs/write
)
```

### Real-world auto-mode demo (slow_pi + slow_sqrt2, 5 M iterations)

```
plain python (cold) :  1.80 s
rote run (cold)   :  2.39 s   (+33% — AST transform + cache write overhead)
rote run (warm)   :  0.52 s   (3.4× faster, 1.28 s saved per re-run)
rote run (warm)   :  0.54 s   (consistent)
```

## Serializer microbench (modernized paper Figure 6)

| Object | Size (MB) | Serializer | rote write (ms) | pickle write (ms) | rote read (ms) | pickle read (ms) |
|---|---|---|---|---|---|---|
| numpy 1 M float64 | 7.63 | numpy | 1.11 | 0.76 | 0.58 | 0.23 |
| numpy 3 M float32 | 11.44 | numpy | **0.78** | 1.90 | 0.92 | 0.47 |
| arrow 1 M rows | 15.26 | arrow | **3.32** | 5.42 | 0.46 | 0.44 |
| dict 100 K items | 1.01 | msgpack | 70.06 | 28.06 | 65.94 | 23.50 |
| list 1 M ints | 4.64 | msgpack | 391.91 | 12.51 | **26.06** | 26.86 |

**Reading the table.** For arrays and DataFrames (the cases that matter for
real research workloads), rote stays in the sub-ms to low-ms range and beats
pickle on larger writes, while small reads can still favor pickle. For very
large homogeneous Python containers — million-entry lists/dicts — msgpack is
slower than pickle because Python-side iteration costs dominate; for those cases
the fallback to cloudpickle is automatic when a smarter serializer applies.

## Where rote does not (yet) win

* Cold-cache write overhead for extremely tiny calls (<1 ms). The
  ``min_duration_s`` threshold (default 1 s) prevents caching these in the
  first place; if you lower it, the writes can dominate.
* Pickle is faster for million-element Python primitive containers. If the
  fast path is "pickle a dict of 100 K ints over and over," joblib wins.
  Use Arrow/numpy for that kind of data.

## Methodology notes

* "Cold" is the first call after a fresh cache. "Warm" is the second call.
* Numbers are minimums of 5 runs (less noisy than means for short
  microbenchmarks).
* No background processes were killed for these numbers — the machine had
  normal IDE/CI processes running. Take ±10% as the noise floor.
* Reproduce: `uv run pytest bench/ -m bench`; raw JSON in `bench/results/`.
