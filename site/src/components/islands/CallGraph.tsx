import { useCallback, useMemo, useState } from 'react';
import {
  Background,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { motion } from 'motion/react';
import { pipeline } from '../../data/pipeline';

/**
 * Call graph visualisation — paper §3.4 / Figure 4.
 *
 * Each pipeline stage is a node. Edges encode the read-dependency between
 * stages. Clicking a node "edits" it: that node's AST hash changes (the
 * label updates) and every transitive downstream caller turns red. Upstream
 * nodes stay green to show they're still served from cache.
 */

interface NodeData extends Record<string, unknown> {
  label: string;
  desc: string;
  state: 'fresh' | 'edited' | 'invalidated';
}

function PipelineNode({ data }: NodeProps<Node<NodeData>>) {
  const tone =
    data.state === 'fresh'
      ? { fill: 'var(--color-rote-soft)', stroke: 'var(--color-rote)', text: 'var(--color-rote)' }
      : data.state === 'edited'
        ? { fill: '#f4c5b3', stroke: 'var(--color-warn)', text: 'var(--color-warn)' }
        : { fill: '#ead4cc', stroke: 'var(--color-warn)', text: 'var(--color-warn)' };
  return (
    <motion.div
      layout
      transition={{ type: 'spring', stiffness: 220, damping: 26 }}
      style={{
        background: tone.fill,
        border: `1.5px solid ${tone.stroke}`,
        borderRadius: 6,
        color: tone.text,
        padding: '8px 14px',
        minWidth: 132,
        textAlign: 'center',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: tone.stroke }} />
      <div style={{ fontWeight: 600, fontSize: 14, letterSpacing: '-0.01em' }}>{String(data.label)}</div>
      <div style={{ fontSize: 11, opacity: 0.85, marginTop: 2 }}>{String(data.desc)}</div>
      <div
        style={{
          marginTop: 6,
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          color: 'var(--color-ink-faint)',
          letterSpacing: '0.04em',
        }}
      >
        {data.state === 'edited' ? 'AST hash changed' : data.state === 'invalidated' ? 'downstream miss' : 'cache hit'}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: tone.stroke }} />
    </motion.div>
  );
}

const nodeTypes = { stage: PipelineNode };

export default function CallGraph() {
  return (
    <section id="graph" className="container-wide mt-24 scroll-mt-24" aria-labelledby="graph-h">
      <header className="mb-8 max-w-3xl">
        <p className="eyebrow">06 — Call graph</p>
        <h2 id="graph-h" className="h-section mt-3">
          How invalidation moves through a dependent pipeline
        </h2>
        <p className="lede mt-4">
          Click any stage. rote rehashes its canonical AST; the new hash mismatches the cached
          entry, so that stage misses. Anything downstream that read its output also misses.
          Anything upstream is untouched. This is the §3.4 mechanism in the paper, redrawn so
          the propagation is visible rather than described.
        </p>
      </header>
      <div className="card p-5 sm:p-7">
        <ReactFlowProvider>
          <Inner />
        </ReactFlowProvider>
      </div>
    </section>
  );
}

function Inner() {
  const [editedIdx, setEditedIdx] = useState<number | null>(null);

  const { nodes, edges } = useMemo(() => {
    const n: Node<NodeData>[] = pipeline.map((s, i) => {
      let state: NodeData['state'] = 'fresh';
      if (editedIdx !== null) {
        if (i === editedIdx) state = 'edited';
        else if (i > editedIdx) state = 'invalidated';
      }
      return {
        id: s.id,
        type: 'stage',
        position: { x: i * 200, y: 60 + (i % 2) * 40 },
        data: { label: s.label, desc: s.desc, state },
      } satisfies Node<NodeData>;
    });
    const e: Edge[] = pipeline.slice(0, -1).map((s, i) => ({
      id: `${s.id}-${pipeline[i + 1].id}`,
      source: s.id,
      target: pipeline[i + 1].id,
      animated: editedIdx !== null && i >= editedIdx,
      style: {
        stroke:
          editedIdx !== null && i >= editedIdx
            ? 'var(--color-warn)'
            : 'var(--color-rote)',
        strokeWidth: 1.5,
      },
    }));
    return { nodes: n, edges: e };
  }, [editedIdx]);

  const onNodeClick = useCallback((_event: unknown, node: Node) => {
    const idx = pipeline.findIndex((s) => s.id === node.id);
    setEditedIdx(idx);
  }, []);

  return (
    <div>
      <div style={{ height: 280 }}>
        <ReactFlow<Node<NodeData>>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.25 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          panOnDrag={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          panOnScroll={false}
        >
          <Background color="var(--color-rule-soft)" gap={20} size={1} />
        </ReactFlow>
      </div>
      <div className="mt-4 flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="text-sm text-[var(--color-ink-soft)]" aria-live="polite">
          {editedIdx === null
            ? 'Click a stage to edit it.'
            : editedIdx === 0
              ? `You edited ${pipeline[editedIdx].label}. Everything downstream misses; nothing upstream to keep.`
              : `You edited ${pipeline[editedIdx].label}. ${pipeline
                  .slice(0, editedIdx)
                  .map((s) => s.label)
                  .join(', ')} stay cached; ${pipeline
                  .slice(editedIdx)
                  .map((s) => s.label)
                  .join(', ')} re-run.`}
        </p>
        <button
          type="button"
          onClick={() => setEditedIdx(null)}
          className="pill"
          disabled={editedIdx === null}
        >
          reset
        </button>
      </div>
    </div>
  );
}
