import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { pipeline, pipelineTotal } from '../../data/pipeline';
import { crossProcessPipeline, inProcessPipeline } from '../../data/bench';
import { fmtSeconds } from '../../lib/format';

type Mode = 'crossProcess' | 'inProcess';

/**
 * The edit-rerun loop. Two horizontal timelines stacked:
 *  - "plain Python" re-runs all four stages
 *  - "rote warm"   re-runs only the changed stage; upstream cells are
 *                  filled with their cached value
 *
 * The viewer picks which stage they "edited"; the visualisation updates.
 * Default to cross-process timings — that's the workflow the paper §4.2
 * actually measured (fresh interpreter each run).
 */
export default function EditRerunLoop() {
  const [editedStage, setEditedStage] = useState(pipeline.length - 1);
  const [mode, setMode] = useState<Mode>('crossProcess');

  const totals = useMemo(() => {
    if (mode === 'crossProcess') {
      return {
        plain: crossProcessPipeline.plain_python_min_s,
        rote: crossProcessPipeline.rote_warm_min_s,
        joblib: crossProcessPipeline.joblib_warm_min_s,
        speedupVsPlain: crossProcessPipeline.rote_speedup_vs_plain,
      };
    }
    return {
      plain: inProcessPipeline.plain_v2_s,
      rote: inProcessPipeline.rote_warm_edit_downstream_s,
      joblib: inProcessPipeline.joblib_warm_edit_downstream_s,
      speedupVsPlain: inProcessPipeline.rote_warm_speedup_vs_plain,
    };
  }, [mode]);

  // For the visual bars we scale stage widths to the *pipeline split*, then
  // map the totals onto the row beneath. The real timing for "plain" is the
  // sum of stages; the real timing for "rote" is the cost of the edited
  // stage plus a small cache-lookup overhead.
  const plainBars = pipeline.map((s) => s.plainSeconds / pipelineTotal);

  return (
    <section id="loop" className="container-wide mt-24 scroll-mt-24" aria-labelledby="loop-h">
      <header className="mb-8 max-w-3xl">
        <p className="eyebrow">02 — Edit-rerun loop</p>
        <h2 id="loop-h" className="h-section mt-3">
          What the cache buys you, stage by stage
        </h2>
        <p className="lede mt-4">
          Pick which stage you just edited. Plain Python re-runs the whole pipeline; rote re-runs
          only the stages whose AST or inputs changed. Numbers come from{' '}
          <code>bench/results/cross_process_pipeline.json</code>, which measures the workflow paper
          §4.2 actually evaluated — a fresh interpreter on every run.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2">
        <fieldset className="inline-flex items-center gap-2" aria-label="Edit which stage?">
          <legend className="eyebrow mr-3">Edited stage</legend>
          {pipeline.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setEditedStage(i)}
              aria-pressed={editedStage === i}
              className={`pill ${
                editedStage === i ? 'pill-rote' : ''
              } transition-colors hover:bg-white/70`}
            >
              {s.label}
            </button>
          ))}
        </fieldset>

        <div className="ml-auto inline-flex items-center gap-2">
          <span className="eyebrow mr-1">Timings</span>
          <button
            type="button"
            onClick={() => setMode('crossProcess')}
            aria-pressed={mode === 'crossProcess'}
            className={`pill ${mode === 'crossProcess' ? 'pill-rote' : ''}`}
          >
            cross-process
          </button>
          <button
            type="button"
            onClick={() => setMode('inProcess')}
            aria-pressed={mode === 'inProcess'}
            className={`pill ${mode === 'inProcess' ? 'pill-rote' : ''}`}
          >
            in-process
          </button>
        </div>
      </div>

      <div className="card p-5 sm:p-7">
        <TimelineRow
          label="plain Python"
          sublabel={`${fmtSeconds(totals.plain)} total — every stage re-runs`}
          bars={plainBars.map((w, i) => ({
            width: w,
            mode: 'compute' as const,
            stage: pipeline[i],
            edited: i === editedStage,
          }))}
          tone="paper"
        />
        <div className="mt-5">
          <TimelineRow
            label="rote (warm)"
            sublabel={`${fmtSeconds(totals.rote)} total · ${totals.speedupVsPlain.toFixed(1)}× over plain`}
            bars={pipeline.map((stage, i) => ({
              width: plainBars[i],
              mode: i < editedStage ? ('cached' as const) : ('compute' as const),
              stage,
              edited: i === editedStage,
            }))}
            tone="rote"
            totalDuration={totals.rote}
          />
        </div>
        <p className="mt-5 text-sm text-[var(--color-ink-soft)]" aria-live="polite">
          You edited <strong>{pipeline[editedStage].label}</strong>.{' '}
          {editedStage === 0 ? (
            <>Nothing upstream to cache. rote still pays just the cache write.</>
          ) : (
            <>
              Stages {pipeline
                .slice(0, editedStage)
                .map((s) => s.label)
                .join(', ')}{' '}
              are served from cache. Stage {pipeline[editedStage].label} recomputes; downstream
              stages re-run because their inputs changed.
            </>
          )}
        </p>
      </div>

      <aside className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--color-ink-faint)]">
        <span>
          Source: <code>bench/results/{mode === 'crossProcess' ? 'cross_process_pipeline.json' : 'paper_pipeline.json'}</code>
        </span>
        <span>
          joblib warm: {fmtSeconds(totals.joblib)} — wins on this benchmark because it skips
          file-content validation.
        </span>
      </aside>
    </section>
  );
}

interface TimelineRowProps {
  label: string;
  sublabel: string;
  bars: { width: number; mode: 'compute' | 'cached'; stage: (typeof pipeline)[number]; edited: boolean }[];
  tone: 'paper' | 'rote';
  totalDuration?: number;
}

function TimelineRow({ label, sublabel, bars, tone }: TimelineRowProps) {
  const computeFill = tone === 'rote' ? 'var(--color-rote-soft)' : 'var(--color-paper-soft)';
  const computeStroke = tone === 'rote' ? 'var(--color-rote)' : 'var(--color-paper)';

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span
          className={`pill ${tone === 'rote' ? 'pill-rote' : 'pill-paper'}`}
          aria-hidden
        >
          {label}
        </span>
        <span className="cite">{sublabel}</span>
      </div>
      <div className="mt-2 flex h-12 w-full overflow-hidden rounded-sm" aria-label={`${label} timeline`}>
        {bars.map((b, i) => (
          <div
            key={b.stage.id}
            className="relative h-full"
            style={{
              width: `${Math.max(b.width * 100, 6)}%`,
              borderRight: i < bars.length - 1 ? '1px solid var(--color-page)' : 'none',
            }}
          >
            <AnimatePresence mode="wait" initial={false}>
              {b.mode === 'compute' ? (
                <motion.div
                  key="compute"
                  initial={{ opacity: 0.4 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ type: 'spring', stiffness: 220, damping: 28 }}
                  className="absolute inset-0 flex flex-col justify-center px-2"
                  style={{ background: computeFill, color: computeStroke, borderTop: `2px solid ${computeStroke}` }}
                >
                  <span className="text-[0.7rem] font-medium uppercase tracking-wider">
                    {b.stage.label}
                  </span>
                  <span className="num text-[0.65rem]">
                    {b.edited ? 'edited' : 'recompute'}
                  </span>
                </motion.div>
              ) : (
                <motion.div
                  key="cached"
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ type: 'spring', stiffness: 220, damping: 28 }}
                  className="absolute inset-0 flex flex-col justify-center px-2"
                  style={{
                    background:
                      'repeating-linear-gradient(135deg, #e7e2d1, #e7e2d1 6px, #f7f4ec 6px, #f7f4ec 12px)',
                    color: 'var(--color-ink-faint)',
                    borderTop: '1px dashed var(--color-rule)',
                  }}
                >
                  <span className="text-[0.7rem] font-medium uppercase tracking-wider">
                    {b.stage.label}
                  </span>
                  <span className="num text-[0.65rem]">cached</span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
    </div>
  );
}
