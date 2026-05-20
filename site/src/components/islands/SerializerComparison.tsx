import { useMemo, useState } from 'react';
import { Group } from '@visx/group';
import { scaleBand, scaleLinear } from '@visx/scale';
import { Bar } from '@visx/shape';
import { AxisBottom } from '@visx/axis';
import { serializerResults } from '../../data/bench';
import { fmtMs } from '../../lib/format';

type Path = 'write' | 'read';

const labelMap: Record<string, string> = {
  numpy_1M_f64: 'numpy · 1 M float64',
  numpy_3M_f32: 'numpy · 3 M float32',
  arrow_1M_rows: 'arrow · 1 M-row table',
  dict_100k_items: 'dict · 100K items',
  list_1M_ints: 'list · 1 M ints',
};

/**
 * Modernised Figure 6.
 *
 * The paper compared pickle / cPickle / marshal. rote dispatches by type:
 * Arrow → DataFrame, numpy.save → ndarray, msgpack → primitives, cloudpickle
 * as a last-resort fallback. The chart shows where the type-aware dispatch
 * helps (DataFrame + ndarray) and where pickle still wins on big homogeneous
 * Python containers. Both observations belong on the page.
 */
export default function SerializerComparison() {
  const [path, setPath] = useState<Path>('write');

  const rows = useMemo(
    () =>
      serializerResults.map((r) => ({
        key: r.name,
        label: labelMap[r.name] ?? r.name,
        serializer: r.serializer,
        rote: path === 'write' ? r.rote_write_ms : r.rote_read_ms,
        pickle: path === 'write' ? r.pickle_write_ms : r.pickle_read_ms,
      })),
    [path],
  );

  return (
    <section
      id="serializer"
      className="container-wide mt-24 scroll-mt-24"
      aria-labelledby="serializer-h"
    >
      <header className="mb-8 max-w-3xl">
        <p className="eyebrow">06 — Serializers (paper Figure 6, updated)</p>
        <h2 id="serializer-h" className="h-section mt-3">
          Picking a serializer by what the function returns
        </h2>
        <p className="lede mt-4">
          The paper compared three pickle variants. rote uses different serializers depending
          on the return type. PyArrow IPC handles DataFrames, <code>numpy.save</code> handles
          ndarrays, safetensors handles tensors, msgpack handles primitives, and cloudpickle
          is the fallback for anything that doesn't fit those buckets. The chart below also
          shows the workloads where pickle still wins (large homogeneous Python containers),
          since those are the cases where the dispatch decision matters most.
        </p>
      </header>

      <div className="mb-4 flex gap-3">
        <button
          type="button"
          onClick={() => setPath('write')}
          aria-pressed={path === 'write'}
          className={`pill ${path === 'write' ? 'pill-rote' : ''}`}
        >
          write (encode)
        </button>
        <button
          type="button"
          onClick={() => setPath('read')}
          aria-pressed={path === 'read'}
          className={`pill ${path === 'read' ? 'pill-rote' : ''}`}
        >
          read (decode)
        </button>
      </div>

      <div className="card p-5 sm:p-7">
        <SerializerChart rows={rows} path={path} />
        <table className="mt-6 w-full text-left text-sm">
          <thead>
            <tr className="border-b hairline text-xs text-[var(--color-ink-faint)]">
              <th className="py-2 font-medium">Payload</th>
              <th className="py-2 font-medium">rote serializer</th>
              <th className="py-2 text-right font-medium">rote</th>
              <th className="py-2 text-right font-medium">pickle (HIGHEST)</th>
              <th className="py-2 text-right font-medium">ratio</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const ratio = r.pickle / r.rote;
              const winner = ratio > 1 ? 'rote' : 'pickle';
              return (
                <tr key={r.key} className="border-b hairline-soft last:border-0">
                  <td className="py-2 pr-3">{r.label}</td>
                  <td className="py-2 pr-3">
                    <span className="pill pill-rote">{r.serializer}</span>
                  </td>
                  <td className="num py-2 pr-3 text-right">{fmtMs(r.rote)}</td>
                  <td className="num py-2 pr-3 text-right">{fmtMs(r.pickle)}</td>
                  <td
                    className={`num py-2 text-right ${winner === 'rote' ? 'text-[var(--color-rote)]' : 'text-[var(--color-paper)]'}`}
                  >
                    {ratio >= 1 ? `${ratio.toFixed(2)}× faster` : `${(1 / ratio).toFixed(2)}× slower`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="mt-4 text-sm text-[var(--color-ink-soft)]">
          Source: <code>bench/results/serialize_microbench.json</code>. Min of 5 trials per cell.
        </p>
      </div>
    </section>
  );
}

interface ChartProps {
  rows: { key: string; label: string; rote: number; pickle: number }[];
  path: Path;
}

function SerializerChart({ rows, path }: ChartProps) {
  const width = 740;
  const height = 280;
  const margin = { top: 12, right: 12, bottom: 36, left: 170 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const max = Math.max(...rows.flatMap((r) => [r.rote, r.pickle])) * 1.1;

  const yScale = scaleBand<string>({
    domain: rows.map((r) => r.key),
    range: [0, innerH],
    padding: 0.25,
  });
  const xScale = scaleLinear<number>({
    domain: [0, max],
    range: [0, innerW],
  });
  const subBand = scaleBand<'rote' | 'pickle'>({
    domain: ['rote', 'pickle'],
    range: [0, yScale.bandwidth()],
    padding: 0.15,
  });

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="block w-full" role="img" aria-label={`Serialization ${path} times`}>
      <Group left={margin.left} top={margin.top}>
        {rows.map((r) => {
          const y = yScale(r.key) ?? 0;
          return (
            <g key={r.key}>
              <text
                x={-10}
                y={y + yScale.bandwidth() / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontFamily="var(--font-sans)"
                fontSize={12}
                fill="var(--color-ink)"
              >
                {r.label}
              </text>
              <Bar
                x={0}
                y={y + (subBand('rote') ?? 0)}
                width={xScale(r.rote)}
                height={subBand.bandwidth()}
                fill="var(--color-rote-soft)"
                stroke="var(--color-rote)"
                rx={1.5}
              />
              <text
                x={xScale(r.rote) + 4}
                y={y + (subBand('rote') ?? 0) + subBand.bandwidth() / 2}
                dominantBaseline="middle"
                fontFamily="var(--font-mono)"
                fontSize={10}
                fill="var(--color-rote)"
              >
                {fmtMs(r.rote)}
              </text>
              <Bar
                x={0}
                y={y + (subBand('pickle') ?? 0)}
                width={xScale(r.pickle)}
                height={subBand.bandwidth()}
                fill="var(--color-paper-soft)"
                stroke="var(--color-paper)"
                rx={1.5}
              />
              <text
                x={xScale(r.pickle) + 4}
                y={y + (subBand('pickle') ?? 0) + subBand.bandwidth() / 2}
                dominantBaseline="middle"
                fontFamily="var(--font-mono)"
                fontSize={10}
                fill="var(--color-paper)"
              >
                {fmtMs(r.pickle)}
              </text>
            </g>
          );
        })}
        <AxisBottom
          top={innerH}
          scale={xScale}
          tickFormat={(d) => `${d}ms`}
          tickLabelProps={() => ({
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            fill: 'var(--color-ink-faint)',
            textAnchor: 'middle',
          })}
          stroke="var(--color-rule)"
          tickStroke="var(--color-rule)"
        />
      </Group>
      <Group top={6} left={margin.left}>
        <rect width={10} height={10} fill="var(--color-rote-soft)" stroke="var(--color-rote)" />
        <text x={14} y={9} fontFamily="var(--font-sans)" fontSize={11} fill="var(--color-rote)">
          rote dispatch
        </text>
        <rect x={110} width={10} height={10} fill="var(--color-paper-soft)" stroke="var(--color-paper)" />
        <text x={124} y={9} fontFamily="var(--font-sans)" fontSize={11} fill="var(--color-paper)">
          pickle (HIGHEST)
        </text>
      </Group>
    </svg>
  );
}
