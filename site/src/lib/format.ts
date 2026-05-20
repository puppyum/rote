/** Format a duration in seconds with a precision that scales with magnitude. */
export function fmtSeconds(s: number): string {
  if (s < 1e-3) return `${(s * 1e6).toFixed(0)} µs`;
  if (s < 1) return `${(s * 1e3).toFixed(1)} ms`;
  if (s < 10) return `${s.toFixed(2)} s`;
  return `${s.toFixed(1)} s`;
}

/** Format a milliseconds value with appropriate precision. */
export function fmtMs(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  return `${ms.toFixed(0)} ms`;
}

/** Format a speedup factor like 4.8× or 0.5×. */
export function fmtRatio(r: number): string {
  if (r >= 10) return `${r.toFixed(0)}×`;
  if (r >= 1) return `${r.toFixed(1)}×`;
  return `${r.toFixed(2)}×`;
}
