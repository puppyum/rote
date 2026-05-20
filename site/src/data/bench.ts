// Benchmark data: vendored at build time from /bench/results/*.json.
// Source of truth: every number on the site must trace back to these
// files or to a paper section cited in the same component.

import crossProcess from '../../../bench/results/cross_process_pipeline.json';
import paperPipeline from '../../../bench/results/paper_pipeline.json';
import w1 from '../../../bench/results/w1_compute_pi.json';
import w2 from '../../../bench/results/w2_polynomial_pi.json';
import w3 from '../../../bench/results/w3_numpy_qr.json';
import w4 from '../../../bench/results/w4_count_words.json';
import w5 from '../../../bench/results/w5_matrix_invert.json';
import serializer from '../../../bench/results/serialize_microbench.json';

export interface WorkloadResult {
  workload: string;
  plain_cold: number;
  plain_warm: number;
  rote_cold: number;
  rote_warm: number;
  joblib_cold: number;
  joblib_warm: number;
  rote_speedup_vs_joblib_warm: number;
  rote_cold_overhead: number;
}

export interface PaperPipelineResult {
  workload: string;
  n_records: number;
  plain_v1_s: number;
  plain_v2_s: number;
  rote_cold_s: number;
  rote_warm_edit_downstream_s: number;
  rote_warm_speedup_vs_plain: number;
  joblib_cold_s: number;
  joblib_warm_edit_downstream_s: number;
  rote_vs_joblib_warm: number;
}

export interface CrossProcessResult {
  workload: string;
  runs_per_condition: number;
  plain_python_min_s: number;
  rote_warm_min_s: number;
  joblib_warm_min_s: number;
  rote_speedup_vs_plain: number;
  rote_vs_joblib: number;
}

export interface SerializerRow {
  name: string;
  serializer: string;
  size_mb: number;
  pickle_size_mb: number;
  rote_write_ms: number;
  rote_read_ms: number;
  pickle_write_ms: number;
  pickle_read_ms: number;
}

export const workloads: WorkloadResult[] = [
  w1 as WorkloadResult,
  w2 as WorkloadResult,
  w3 as WorkloadResult,
  w4 as WorkloadResult,
  w5 as WorkloadResult,
];

export const crossProcessPipeline = crossProcess as CrossProcessResult;
export const inProcessPipeline = paperPipeline as PaperPipelineResult;
export const serializerResults = serializer as SerializerRow[];

/** Workload display labels (paper §4-style). */
export const workloadLabels: Record<string, { label: string; desc: string }> = {
  w1_compute_pi: { label: 'Leibniz π', desc: '2 M-term series, pure-CPU Python loop' },
  w2_polynomial_pi: { label: 'Basel sum', desc: '2 M-term sum, pure-CPU Python loop' },
  w3_numpy_qr: { label: 'NumPy QR', desc: '400×400 QR decomposition' },
  w4_count_words: { label: 'Bag of words', desc: '200K-char string counter' },
  w5_matrix_invert: { label: 'Matrix inverse', desc: '200×200 NumPy inverse' },
};

/** Geometric mean of rote-vs-joblib warm speedup across the five workloads. */
export function geomeanRoteVsJoblib(): number {
  const ratios = workloads.map((w) => w.rote_speedup_vs_joblib_warm);
  const logSum = ratios.reduce((acc, r) => acc + Math.log(r), 0);
  return Math.exp(logSum / ratios.length);
}
