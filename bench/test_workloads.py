"""Workload benchmarks vs joblib.Memory.

Each workload runs through three paths:
  * plain Python (baseline)
  * rote @cache decorator
  * joblib.Memory

We measure cold (first call) and warm (second call) wall-clock.
Results are written to bench/results/results.json for the BENCHMARKS.md table.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from joblib import Memory

import rote

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ----------- Workloads


def w1_compute_pi(n: int) -> float:
    total = 0.0
    for k in range(n):
        total += ((-1) ** k) / (2 * k + 1)
    return 4.0 * total


def w2_polynomial_pi(n: int) -> float:
    # Different formula, also slow.
    s = 0.0
    for k in range(n):
        s += 1.0 / ((2 * k + 1) * (2 * k + 1))
    return (8.0 * s) ** 0.5


def w3_numpy_qr(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(400, 400))
    _, r = np.linalg.qr(a)
    return r


def w4_count_words(n_chars: int) -> dict:
    s = "the quick brown fox " * (n_chars // 20)
    out: dict = {}
    for w in s.split():
        out[w] = out.get(w, 0) + 1
    return out


def w5_matrix_invert(n: int) -> float:
    rng = np.random.default_rng(n)
    m = rng.normal(size=(n, n)) + n * np.eye(n)
    inv = np.linalg.inv(m)
    return float((inv @ m - np.eye(n)).max())


WORKLOADS = [
    ("w1_compute_pi", w1_compute_pi, 2_000_000),
    ("w2_polynomial_pi", w2_polynomial_pi, 2_000_000),
    ("w3_numpy_qr", w3_numpy_qr, 17),
    ("w4_count_words", w4_count_words, 200_000),
    ("w5_matrix_invert", w5_matrix_invert, 200),
]


# ----------- The benchmark


def _time_two_runs(fn, arg, warm_iters: int = 20):
    """Return (cold, warm) seconds. Warm is the *mean* across warm_iters
    iterations, which is far more stable for sub-millisecond hits than a
    single point measurement.
    """
    t0 = time.perf_counter()
    out1 = fn(arg)
    cold = time.perf_counter() - t0
    # Warm iterations: take the median to discount cold-start noise.
    warms: list[float] = []
    out2 = None
    for _ in range(warm_iters):
        t0 = time.perf_counter()
        out2 = fn(arg)
        warms.append(time.perf_counter() - t0)
    warm = sorted(warms)[warm_iters // 2]
    if isinstance(out1, np.ndarray):
        assert np.allclose(out1, out2)
    else:
        assert out1 == out2
    return cold, warm


@pytest.mark.bench
@pytest.mark.parametrize("name,fn,arg", WORKLOADS, ids=lambda v: str(v) if not callable(v) else v.__name__)
def test_bench_workload(name, fn, arg, tmp_path):
    plain_cold, plain_warm = _time_two_runs(fn, arg)
    # rote
    rote.configure(cache_dir=tmp_path / "rote", min_duration_s=0.0)
    rote_fn = rote.cache(fn)
    incpy_cold, incpy_warm = _time_two_runs(rote_fn, arg)
    # joblib
    mem = Memory(tmp_path / "joblib", verbose=0)
    joblib_fn = mem.cache(fn)
    jl_cold, jl_warm = _time_two_runs(joblib_fn, arg)

    result = {
        "workload": name,
        "plain_cold": plain_cold,
        "plain_warm": plain_warm,
        "rote_cold": incpy_cold,
        "rote_warm": incpy_warm,
        "joblib_cold": jl_cold,
        "joblib_warm": jl_warm,
        "rote_speedup_vs_joblib_warm": jl_warm / max(incpy_warm, 1e-9),
        "rote_cold_overhead": (incpy_cold - plain_cold) / max(plain_cold, 1e-9),
    }
    out_file = RESULTS_DIR / f"{name}.json"
    out_file.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
