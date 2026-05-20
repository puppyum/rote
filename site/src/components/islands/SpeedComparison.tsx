import { useMemo, useState } from 'react';
import { Group } from '@visx/group';
import { scaleBand, scaleLinear } from '@visx/scale';
import { Bar } from '@visx/shape';
import { AxisLeft } from '@visx/axis';
import {
  crossProcessPipeline,
  geomeanRoteVsJoblib,
  workloadLabels,
  workloads,
} from '../../data/bench';
import { fmtSeconds, fmtRatio } from '../../lib/format';

type View = 'vsPaper' | 'vsJoblib';

/**
 * Two sub-tables in one widget.
 *
 *  - vsPaper  : the paper's reported ~10× edit-rerun number next to rote's
 *               ~5× cross-process number. In-process ~48× goes in a footnote.
 *  - vsJoblib : head-to-head warm-hit cost across the five workloads, with
 *               the one row where joblib *wins* (cross-process) called out
 *               so the careful reader doesn't have to hunt for it.
 */
export default function SpeedComparison() {
  const [view, setView] = useState<View>('vsPaper');
  const geomean = useMemo(() => geomeanRoteVsJoblib(), []);

  return (
    <section id="speed" className="container-wide mt-24 scroll-mt-24" aria-labelledby="speed-h">
      <header className="mb-8 max-w-3xl">
        <p className="eyebrow">05 — Speedups</p>
        <h2 id="speed-h" className="h-section mt-3">
          Two ways to measure how fast it is
        </h2>
        <p className="lede mt-4">
          The first reference point is the speedup the original paper reported in 2011. The
          second is joblib, which is the most common memoization library for Python research
          scripts today. Both sets of numbers come from <code>bench/results/*.json</code>.
          The toggle below picks which one to look at first.
        </p>
      </header>

      <div className="mb-4 flex gap-3">
        <button
          type="button"
          onClick={() => setView('vsPaper')}
          aria-pressed={view === 'vsPaper'}
          className={`pill ${view === 'vsPaper' ? 'pill-rote' : ''}`}
        >
          vs the 2011 paper
        </button>
        <button
          type="button"
          onClick={() => setView('vsJoblib')}
          aria-pressed={view === 'vsJoblib'}
          className={`pill ${view === 'vsJoblib' ? 'pill-rote' : ''}`}
        >
          vs joblib (warm, per-call)
        </button>
      </div>

      {view === 'vsPaper' ? (
        <VsPaper />
      ) : (
        <VsJoblib geomean={geomean} />
      )}
    </section>
  );
}

function VsPaper() {
  return (
    <div className="card p-5 sm:p-7">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b hairline text-sm text-[var(--color-ink-faint)]">
            <th className="py-3 font-medium">Comparison</th>
            <th className="py-3 font-medium">Paper (2011)</th>
            <th className="py-3 font-medium">rote (2026)</th>
            <th className="py-3 text-right font-medium">Source</th>
          </tr>
        </thead>
        <tbody className="text-base">
          <tr className="border-b hairline-soft align-top">
            <td className="py-4 pr-4">
              Edit-rerun on a multi-stage script,
              <br />
              <span className="cite">fresh interpreter each run</span>
            </td>
            <td className="py-4 pr-4 num">~10×</td>
            <td className="py-4 pr-4 num">
              {fmtRatio(crossProcessPipeline.rote_speedup_vs_plain)}
              <span className="cite ml-1">
                ({fmtSeconds(crossProcessPipeline.plain_python_min_s)} →{' '}
                {fmtSeconds(crossProcessPipeline.rote_warm_min_s)})
              </span>
            </td>
            <td className="py-4 cite text-right">
              cross_process_pipeline.json · paper §4.2
            </td>
          </tr>
          <tr className="align-top">
            <td className="py-4 pr-4">
              Same pipeline,
              <br />
              <span className="cite">one interpreter, LRU pre-warmed</span>
            </td>
            <td className="py-4 pr-4 text-[var(--color-ink-faint)]">
              not measured separately
            </td>
            <td className="py-4 pr-4 num">~48×</td>
            <td className="py-4 cite text-right">paper_pipeline.json</td>
          </tr>
        </tbody>
      </table>
      <p className="mt-5 text-sm text-[var(--color-ink-soft)]">
        The cross-process row is the one that lines up with the paper's measurement. Roughly
        half the paper's reported speedup is the order of magnitude we'd expect after fifteen
        years of hardware progress, plus the cost of rote content-hashing every file
        dependency on every hit. The in-process number is the upper bound once interpreter
        startup is amortised; it's listed here as a second data point, not the headline.
      </p>
    </div>
  );
}

function VsJoblib({ geomean }: { geomean: number }) {
  const maxRatio = Math.max(
    ...workloads.map((w) => w.rote_speedup_vs_joblib_warm),
    crossProcessPipeline.rote_vs_joblib,
  );

  const width = 720;
  const height = 320;
  const margin = { top: 16, right: 24, bottom: 32, left: 200 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const rows = useMemo(
    () => [
      ...workloads.map((w) => ({
        key: w.workload,
        label: workloadLabels[w.workload]?.label ?? w.workload,
        ratio: w.rote_speedup_vs_joblib_warm,
        roteWarm: w.rote_warm,
        joblibWarm: w.joblib_warm,
        loses: false,
      })),
      {
        key: 'cross_process',
        label: 'cross-process pipeline',
        ratio: crossProcessPipeline.rote_vs_joblib,
        roteWarm: crossProcessPipeline.rote_warm_min_s,
        joblibWarm: crossProcessPipeline.joblib_warm_min_s,
        loses: crossProcessPipeline.rote_vs_joblib < 1,
      },
    ],
    [],
  );

  const yScale = scaleBand<string>({
    domain: rows.map((r) => r.key),
    range: [0, innerH],
    padding: 0.3,
  });
  const xScale = scaleLinear<number>({
    domain: [0, Math.max(maxRatio * 1.1, 1.5)],
    range: [0, innerW],
  });

  return (
    <div className="card p-5 sm:p-7">
      <div className="mb-3 flex flex-wrap items-baseline justify-between">
        <p className="text-base text-[var(--color-ink-soft)]">
          Across the five per-call workloads, rote is on average <strong>{fmtRatio(geomean)}</strong>{' '}
          faster than joblib on the warm path. The bottom row in the chart is the cross-process
          pipeline, which is the one where joblib actually wins by about a factor of{' '}
          {(1 / crossProcessPipeline.rote_vs_joblib).toFixed(1)}×; it skips the content-hash
          validation rote runs on every hit. That trade-off is the point of the chart.
        </p>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="block w-full" role="img" aria-label="rote vs joblib warm-hit speedup">
        <Group left={margin.left} top={margin.top}>
          <AxisLeft
            scale={yScale}
            tickFormat={(d) => rows.find((r) => r.key === d)?.label ?? ''}
            tickLabelProps={() => ({
              fontFamily: 'var(--font-sans)',
              fontSize: 13,
              fill: 'var(--color-ink)',
              textAnchor: 'end',
              dy: '0.33em',
              dx: -8,
            })}
            stroke="var(--color-rule)"
            tickStroke="var(--color-rule)"
          />
          {/* 1× reference line */}
          <line
            x1={xScale(1)}
            x2={xScale(1)}
            y1={0}
            y2={innerH}
            stroke="var(--color-ink-faint)"
            strokeDasharray="3 3"
          />
          <text
            x={xScale(1) + 4}
            y={-2}
            fontFamily="var(--font-sans)"
            fontSize={10}
            fill="var(--color-ink-faint)"
          >
            1× (parity)
          </text>
          {rows.map((r) => {
            const y = yScale(r.key) ?? 0;
            const h = yScale.bandwidth();
            const bar = xScale(r.ratio);
            const tone = r.loses ? 'var(--color-paper)' : 'var(--color-rote)';
            const fill = r.loses ? 'var(--color-paper-soft)' : 'var(--color-rote-soft)';
            return (
              <g key={r.key}>
                <Bar
                  x={0}
                  y={y}
                  width={bar}
                  height={h}
                  fill={fill}
                  stroke={tone}
                  strokeWidth={1}
                  rx={2}
                />
                <text
                  x={bar + 6}
                  y={y + h / 2}
                  dominantBaseline="middle"
                  fontFamily="var(--font-mono)"
                  fontSize={12}
                  fill="var(--color-ink)"
                  data-loses={r.loses ? 'true' : 'false'}
                >
                  {r.loses ? `${fmtRatio(r.ratio)} — joblib wins` : fmtRatio(r.ratio)}
                </text>
                <title>
                  {r.label}: rote {fmtSeconds(r.roteWarm)} vs joblib {fmtSeconds(r.joblibWarm)}
                </title>
              </g>
            );
          })}
        </Group>
      </svg>
      <p className="mt-4 text-sm text-[var(--color-ink-soft)]">
        Hardware: Apple Silicon, Python 3.13, NVMe. Per-workload warm timings are medians of 20
        iterations; the cross-process row is the minimum of 5 fresh subprocess invocations.
        Reproduce with <code>pytest bench/ -m bench</code>.
      </p>
    </div>
  );
}
