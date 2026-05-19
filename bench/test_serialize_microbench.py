"""Microbench: serializer throughput (paper Figure 6 modernized).

Writes results to bench/results/serialize_microbench.json.
"""

from __future__ import annotations

import json
import pickle
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

from rote.serialize import decode, encode

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _time(fn, repeat=5):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return min(times)


def _bench_value(name, value):
    n_enc, data = encode(value)
    size_mb = len(data) / (1 << 20)
    enc_ms = _time(lambda: encode(value)) * 1000
    dec_ms = _time(lambda: decode(n_enc, data)) * 1000
    pkl = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    pkl_enc_ms = _time(lambda: pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)) * 1000
    pkl_dec_ms = _time(lambda: pickle.loads(pkl)) * 1000
    return {
        "name": name,
        "serializer": n_enc,
        "size_mb": size_mb,
        "pickle_size_mb": len(pkl) / (1 << 20),
        "rote_write_ms": enc_ms,
        "rote_read_ms": dec_ms,
        "pickle_write_ms": pkl_enc_ms,
        "pickle_read_ms": pkl_dec_ms,
    }


def main():
    rng = np.random.default_rng(0)
    cases = [
        ("numpy_1M_f64", rng.normal(size=1_000_000)),
        ("numpy_3M_f32", rng.normal(size=3_000_000).astype("float32")),
        ("arrow_1M_rows", pa.table({"x": rng.integers(0, 1000, 1_000_000),
                                    "y": rng.normal(size=1_000_000)})),
        ("dict_100k_items", {f"k{i}": int(i) for i in range(100_000)}),
        ("list_1M_ints", [int(i) for i in range(1_000_000)]),
    ]
    results = [_bench_value(n, v) for n, v in cases]
    out = RESULTS_DIR / "serialize_microbench.json"
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


def test_serialize_microbench():
    main()


if __name__ == "__main__":
    main()
