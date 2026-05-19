"""Paper §4-style workload: multi-stage pipeline with a downstream edit.

The paper's headline claim (Figure 1 + §4.2) is that editing a downstream
stage in a multi-stage analysis should re-run only that stage, not the
whole pipeline. We model this with file-passed intermediate state, which
matches how research scripts actually share data between stages and lets
rote's file-dep tracking shine.

Pipeline shape:
    seed (int)        →  stage_a_synth   →  "data.json" on disk
    "data.json"       →  stage_b_summary →  "summary.json" on disk
    "summary.json"    →  stage_c_format  →  string

Editing stage_c only invalidates stage_c's cache. stage_a and stage_b are
served from disk, but stage_b also needs to re-read summary.json — which
hasn't changed, so its file-dep hash matches.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from joblib import Memory

import rote

RESULTS_DIR = Path(__file__).resolve().parent / "results"
N_RECORDS = 200_000


def stage_a_synth(seed: int, out_path: str) -> str:
    """Synthesize records and write to disk; returns the path."""
    data = []
    for i in range(N_RECORDS):
        x = ((seed + i) * 37) % 1000
        y = ((seed + i) * 13) % 500
        data.append({"id": i, "x": x, "y": y})
    Path(out_path).write_text(json.dumps(data))
    return out_path


def stage_b_summary(in_path: str, out_path: str) -> str:
    """Read records, aggregate, write summary to disk."""
    data = json.loads(Path(in_path).read_text())
    n = len(data)
    sum_x = sum(r["x"] for r in data)
    sum_y = sum(r["y"] for r in data)
    summary = {
        "n": n,
        "mean_x": sum_x / n,
        "mean_y": sum_y / n,
        "ratio": sum_x / max(sum_y, 1),
    }
    Path(out_path).write_text(json.dumps(summary))
    return out_path


def stage_c_format_v1(in_path: str) -> str:
    """Read summary, format as scientific."""
    s = json.loads(Path(in_path).read_text())
    return f"n={s['n']:.2e} mean_x={s['mean_x']:.3e} ratio={s['ratio']:.3e}"


def stage_c_format_v2(in_path: str) -> str:
    """Read summary, format as decimal — the 'edited' downstream stage."""
    s = json.loads(Path(in_path).read_text())
    return f"n={s['n']:.0f} mean_x={s['mean_x']:.4f} ratio={s['ratio']:.4f}"


def _pipeline(parse, agg, fmt, data_path: str, summary_path: str) -> tuple[float, str]:
    t0 = time.perf_counter()
    parse(42, data_path)
    agg(data_path, summary_path)
    out = fmt(summary_path)
    return time.perf_counter() - t0, out


@pytest.mark.bench
def test_paper_pipeline_edit_downstream(tmp_path):
    """Edit the final stage; expect upstream stages to be served from cache."""
    data_path = str(tmp_path / "data.json")
    summary_path = str(tmp_path / "summary.json")

    # ----- Plain Python: each run does ALL the work.
    plain_v1, out_v1 = _pipeline(stage_a_synth, stage_b_summary, stage_c_format_v1, data_path, summary_path)
    plain_v2, out_v2 = _pipeline(stage_a_synth, stage_b_summary, stage_c_format_v2, data_path, summary_path)

    # ----- rote: stages cached, with file-dep tracking.
    # Reset session state explicitly so prior tests don't leak in.
    from rote import session as _sess

    _sess._reset_for_testing()
    rote.configure(cache_dir=tmp_path / "i", min_duration_s=0.0)
    ip_synth = rote.cache(stage_a_synth)
    ip_summary = rote.cache(stage_b_summary)
    ip_v1 = rote.cache(stage_c_format_v1)
    ip_v2 = rote.cache(stage_c_format_v2)
    ip_cold, _ = _pipeline(ip_synth, ip_summary, ip_v1, data_path, summary_path)
    ip_warm, ip_warm_out = _pipeline(ip_synth, ip_summary, ip_v2, data_path, summary_path)
    print("---rote stats---")
    print(json.dumps(rote.stats(), indent=2))

    # ----- joblib: same with its decorator.
    mem = Memory(tmp_path / "j", verbose=0)
    jl_synth = mem.cache(stage_a_synth)
    jl_summary = mem.cache(stage_b_summary)
    jl_v1 = mem.cache(stage_c_format_v1)
    jl_v2 = mem.cache(stage_c_format_v2)
    jl_data = str(tmp_path / "j_data.json")
    jl_summary_path = str(tmp_path / "j_summary.json")
    jl_cold, _ = _pipeline(jl_synth, jl_summary, jl_v1, jl_data, jl_summary_path)
    jl_warm, _ = _pipeline(jl_synth, jl_summary, jl_v2, jl_data, jl_summary_path)

    result = {
        "workload": "paper_pipeline_edit_downstream",
        "n_records": N_RECORDS,
        "plain_v1_s": plain_v1,
        "plain_v2_s": plain_v2,
        "rote_cold_s": ip_cold,
        "rote_warm_edit_downstream_s": ip_warm,
        "rote_warm_speedup_vs_plain": plain_v2 / max(ip_warm, 1e-9),
        "joblib_cold_s": jl_cold,
        "joblib_warm_edit_downstream_s": jl_warm,
        "rote_vs_joblib_warm": jl_warm / max(ip_warm, 1e-9),
    }
    print(json.dumps(result, indent=2))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "paper_pipeline.json").write_text(json.dumps(result, indent=2))

    # Correctness
    assert out_v1 != out_v2
    assert ip_warm_out == out_v2

    # Paper claim: edit-downstream warm run skips the upstream cost.
    # Even allowing 50% slack for serialization overhead, the warm run
    # should beat plain Python by a healthy margin since stages a + b
    # (~80% of plain runtime) are cached.
    assert ip_warm < plain_v2 * 0.5, (
        f"rote warm-edit-downstream not faster: warm={ip_warm:.3f}s vs plain={plain_v2:.3f}s"
    )
