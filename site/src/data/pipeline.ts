/**
 * Sample pipeline data for the edit-rerun loop widget and the call-graph
 * widget. Stage timings are pulled from the real cross-process pipeline
 * (1.83 s plain → 0.38 s warm) but pro-rated across four stages because
 * the dashboard's animated demo splits "parse → aggregate → train →
 * plot" while the bench measures three stages.
 *
 * The numbers above each bar reflect a plausible four-stage split of the
 * 1.83 s plain Python total — the *visual* is the point; the numeric
 * total at the right edge matches `cross_process_pipeline.json`.
 *
 * Edit `plot`: plain re-runs all four; rote re-runs only `plot` and
 * serves the upstream three from cache.
 */

export interface Stage {
  id: string;
  label: string;
  desc: string;
  /** Pro-rated cost of this stage in seconds for the plain re-run. */
  plainSeconds: number;
  /** "depends on" — used for the call graph and for invalidation. */
  inputs: string[];
  /** "produces" — files written by this stage. */
  outputs: string[];
}

export const pipeline: Stage[] = [
  {
    id: 'parse',
    label: 'parse',
    desc: 'Read CSV → records list',
    plainSeconds: 0.42,
    inputs: ['raw.csv'],
    outputs: ['records.json'],
  },
  {
    id: 'aggregate',
    label: 'aggregate',
    desc: 'Records → per-bucket summaries',
    plainSeconds: 0.61,
    inputs: ['records.json'],
    outputs: ['summary.json'],
  },
  {
    id: 'train',
    label: 'train',
    desc: 'Fit a small regression on the summary',
    plainSeconds: 0.66,
    inputs: ['summary.json'],
    outputs: ['model.pkl'],
  },
  {
    id: 'plot',
    label: 'plot',
    desc: 'Render the figure used in the report',
    plainSeconds: 0.14,
    inputs: ['model.pkl', 'summary.json'],
    outputs: ['fig.png'],
  },
];

/** Total of the four stages above. Used to scale the timeline width. */
export const pipelineTotal = pipeline.reduce((s, st) => s + st.plainSeconds, 0);
