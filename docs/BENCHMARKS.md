# Benchmarks

All benchmarks run on Python 3.13, Apple Silicon (M-series), macOS, NVMe SSD.
Reproduce with `uv run pytest bench/ -m bench --benchmark-disable`. Raw JSON
lands in `bench/results/`.

For workload microbenches, each test reports the median of 20 warm-cache
iterations. For the paper-style pipeline and the cross-process pipeline, the
table below shows the median across 5 independent runs.

## Workload matrix

| # | Workload | What it measures |
|---|---|---|
| 1 | w1_compute_pi | 2 M-term Leibniz series (pure CPU, Python loop) |
| 2 | w2_polynomial_pi | Basel sum, similar pure-CPU workload, different formula |
| 3 | w3_numpy_qr | 400×400 QR decomposition on NumPy arrays |
| 4 | w4_count_words | Bag-of-words count over a 200K-character string |
| 5 | w5_matrix_invert | 200×200 matrix inverse via NumPy |

## Warm-hit cost vs `joblib.Memory` (in-process, per call)

| Workload | joblib warm | rote warm | speedup |
|---|---|---|---|
| w1_compute_pi | 101 µs | 49 µs | **2.06×** |
| w2_polynomial_pi | 90 µs | 35 µs | **2.58×** |
| w3_numpy_qr | 226 µs | 35 µs | **6.40×** |
| w4_count_words | 98 µs | 37 µs | **2.64×** |
| w5_matrix_invert | 88 µs | 69 µs | **1.29×** |

Geometric mean: **2.59× faster than joblib** across the five workloads.

## Paper-style edit-rerun pipeline (in-process)

A three-stage pipeline (parse → aggregate → format) where the user edits the
final stage and re-runs. Upstream stages are served from cache.

```
plain Python (cold, v2):                  252 ms
rote (cold, first run):                   369 ms   (cache write overhead)
rote (warm, edit downstream):             5.5 ms   (~46× faster than plain)
joblib (warm, edit downstream):           1.8 ms
```

Joblib wins on this benchmark because it keys purely on argument values.
`rote` content-hashes the intermediate JSON files on every hit, so a
mtime-preserving overwrite cannot return a stale result. The
`tests/unit/test_file_hash_cache.py` adversarial test pins this invariant —
including the rewound-mtime case where ctime_ns is the only signal left that
the content actually changed.

## Cross-process pipeline (the actual edit-save-rerun loop)

In-process timings hide what the user feels at the terminal: each "save and
re-run" starts a fresh Python interpreter. The in-process file-content LRU is
empty there. The persistent stat → content-hash table in the SQLite cache is
what keeps file-dep validation cheap.

Workload here is the same pipeline shape, scaled up so plain Python's runtime
exceeds the subprocess startup cost. Each row is the minimum of 5 fresh
subprocess invocations.

| | wall-clock |
|---|---|
| plain Python (whole pipeline) | 1.75 s |
| `rote` warm (fresh interpreter) | 0.35 s |
| `joblib` warm (fresh interpreter) | 0.19 s |

`rote` is **4.9× faster than re-running the script unaided**. Joblib stays
about 2× ahead cross-process, for the same content-validation reason as
above.

## Hot-path microbench (per-hit cost)

| Configuration | µs per hit |
|---|---|
| Baseline (pre-optimization) | 120 µs |
| After single-query + lazy hits + no fsync | 54 µs |
| After in-memory hit cache | 24 µs |
| After file-dep mem-cache + library filter | 22 µs |
| **After persistent stat → content-hash table** | **22 µs steady-state; +0 µs marginal on cross-process warm** |

Tunable knobs in `Config`:

```python
rote.configure(
    eager_hit_counters=False,  # batch counter updates → −5 µs/hit
    fsync_writes=False,        # skip durability fsync → −500 µs/write
)
```

## Serializer microbench

| Object | Size (MB) | Serializer | rote write (ms) | pickle write (ms) | rote read (ms) | pickle read (ms) |
|---|---|---|---|---|---|---|
| numpy 1 M float64 | 7.63 | numpy | 0.44 | 0.35 | 0.71 | 0.26 |
| numpy 3 M float32 | 11.44 | numpy | **0.66** | 1.12 | 0.89 | 0.56 |
| arrow 1 M rows | 15.26 | arrow | **2.75** | 3.60 | **0.43** | 0.67 |
| dict 100 K items | 1.01 | msgpack | 46.55 | 10.90 | 45.47 | 17.42 |
| list 1 M ints | 4.64 | msgpack | 361.78 | 11.12 | **24.62** | 29.87 |

**Reading the table.** For arrays and DataFrames, `rote` stays in the
sub-ms to low-ms range and beats pickle on larger writes; small reads can
still favor pickle. For very large homogeneous Python containers,
msgpack pays Python-side iteration cost and pickle wins.

## Where rote does not win

- **In-process millisecond pipelines.** When the whole workload is already a
  few milliseconds, joblib's no-validation lookup beats `rote`'s
  content-hashed lookup by a small absolute amount. Acceptable tradeoff for
  the staleness guarantee — and not what `rote` was built to optimize.
- **Cold-cache write overhead for sub-millisecond calls.** The
  `min_duration_s` threshold (default 1 s) prevents caching these in the
  first place; if you lower it, the writes can dominate.
- **Million-element Python primitive containers.** Pickle's C-level
  serialization wins; for that shape of data, prefer Arrow or numpy.

## Methodology notes

- "Cold" is the first call after a fresh cache. "Warm" is the second call.
- Workload warm timings are medians of 20 iterations.
- Pipeline numbers are medians across 5 fresh runs of the full bench.
- No background processes were killed for these numbers — the machine had
  normal IDE/CI processes running. Take ±10% as the noise floor.
- Reproduce: `uv run pytest bench/ -m bench --benchmark-disable`. Raw JSON
  in `bench/results/`.
