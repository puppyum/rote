import { describe, expect, it } from 'vitest';
import {
  crossProcessPipeline,
  geomeanRoteVsJoblib,
  inProcessPipeline,
  serializerResults,
  workloads,
} from './bench';

/**
 * Data-shape and sanity tests for the benchmark imports.
 *
 * These guard against the dashboard silently displaying stale or malformed
 * numbers: if a JSON in `bench/results/` drops a field or changes a name,
 * the dashboard surfaces undefined; these tests fail loudly first.
 */

describe('workloads', () => {
  it('has five entries (w1..w5)', () => {
    expect(workloads).toHaveLength(5);
  });

  it('each row has the expected fields and positive timings', () => {
    for (const w of workloads) {
      expect(w.workload).toMatch(/^w[1-5]_/);
      expect(w.rote_warm).toBeGreaterThan(0);
      expect(w.joblib_warm).toBeGreaterThan(0);
      expect(w.rote_speedup_vs_joblib_warm).toBeGreaterThan(0);
    }
  });
});

describe('crossProcessPipeline', () => {
  it('rote is meaningfully faster than plain', () => {
    expect(crossProcessPipeline.rote_speedup_vs_plain).toBeGreaterThan(2);
  });

  it('joblib still beats rote cross-process (we want to surface this)', () => {
    expect(crossProcessPipeline.rote_vs_joblib).toBeLessThan(1);
  });
});

describe('inProcessPipeline', () => {
  it('warm rote is dramatically faster than plain in-process', () => {
    expect(inProcessPipeline.rote_warm_speedup_vs_plain).toBeGreaterThan(20);
  });
});

describe('serializerResults', () => {
  it('has the expected five workloads', () => {
    expect(serializerResults.map((r) => r.name).sort()).toEqual(
      ['arrow_1M_rows', 'dict_100k_items', 'list_1M_ints', 'numpy_1M_f64', 'numpy_3M_f32'],
    );
  });

  it('rote beats pickle on numpy float32 writes (architectural win)', () => {
    const row = serializerResults.find((r) => r.name === 'numpy_3M_f32')!;
    expect(row.rote_write_ms).toBeLessThan(row.pickle_write_ms);
  });
});

describe('geomeanRoteVsJoblib', () => {
  it('is greater than 1 (rote beats joblib per-call warm)', () => {
    const g = geomeanRoteVsJoblib();
    expect(g).toBeGreaterThan(1);
  });
});
