/**
 * Discrepancy log entries — paper claim, rote measurement, why it differs.
 *
 * Sourced from site/DISCREPANCIES.md so the prose and the dashboard stay in
 * sync. Edit there, mirror here.
 */

export interface Discrepancy {
  topic: string;
  paper: string;
  rote: string;
  why: string;
  citation: string;
  /** Optional: a year-of-platform attribution that made this possible. */
  enabled?: string;
}

export const discrepancies: Discrepancy[] = [
  {
    topic: 'Edit-rerun speedup',
    paper: '~10× on real workflows (fresh interpreter each run).',
    rote: '4.9× cross-process on a paper-shaped pipeline (1.75 s → 0.35 s; joblib 0.19 s).',
    why: 'Roughly half the paper’s factor. Hardware has moved, and rote content-hashes file dependencies on every hit where the paper trusted (size, mtime). The validation costs cycles but closes a stale-result hole.',
    citation: 'paper §4.2 · bench/results/cross_process_pipeline.json',
  },
  {
    topic: 'File-dependency identity',
    paper: 'Keyed on (size, mtime).',
    rote: 'Indexed on (dev, ino); validated against (size, mtime_ns, ctime_ns) in a persistent SQLite table.',
    why: 'A `touch -r` rewinding mtime after a same-size overwrite would fool the paper’s scheme; ctime_ns moves anyway because the kernel updates it on every inode write and userspace can’t backdate it without root.',
    citation:
      'paper §3.5 · tests/unit/test_file_hash_cache.py::test_size_preserving_mtime_backdated_edit_still_invalidates',
  },
  {
    topic: 'Source-change detection',
    paper: 'Coarse source-byte hashing — adding a comment busts the cache.',
    rote: 'Canonical-AST hash via libcst (strips comments, docstrings, annotations; De Bruijn-renames bound variables).',
    why: 'libcst didn’t exist when the paper was written. The newer machinery means cosmetic edits no longer invalidate.',
    citation: 'paper §3.2 · src/rote/identity.py',
    enabled: 'libcst (2019)',
  },
  {
    topic: 'Serialization format',
    paper: 'Pickle variants dominate the warm path (Figure 6).',
    rote: 'Type-dispatched: PyArrow IPC → DataFrame, numpy.save → ndarray, safetensors → tensors, msgpack → primitives, cloudpickle as fallback.',
    why: 'PyArrow IPC, numpy’s zero-copy load path, and safetensors all post-date the paper. For DataFrames and ndarrays — the cases that matter to modern research — they beat pickle. For huge homogeneous Python containers pickle still wins; documented openly.',
    citation: 'paper Figure 6 · bench/results/serialize_microbench.json',
    enabled: 'PyArrow IPC (2016), safetensors (2023)',
  },
  {
    topic: 'Interpreter compatibility',
    paper: 'Required a CPython 2.6.3 patch — a custom interpreter binary that stopped tracking upstream years ago.',
    rote: 'Pure-Python library on stock CPython 3.12+.',
    why: 'PEP 578 (audit hooks) and PEP 669 (sys.monitoring) let user code observe events the 2011 prototype needed an interpreter fork to see.',
    citation: 'paper §1 · pyproject.toml',
    enabled: 'PEP 578 audit hooks (2018), PEP 669 sys.monitoring (2023)',
  },
  {
    topic: 'Concurrency',
    paper: 'Single-process — no shared-cache IPC story.',
    rote: 'Multi-process safe via SQLite WAL + atomic blob rename. 16-process hammer test in tests/correctness/.',
    why: 'A modern research workflow runs notebooks and CLI jobs against the same cache. SQLite WAL didn’t see widespread adoption until ~2010 and the audit-hook scaffolding to keep file dependencies honest under concurrency post-dates the paper too.',
    citation: 'tests/correctness/test_concurrency.py',
  },
  {
    topic: 'Argument-mutation detection',
    paper: 'Not modelled — static analysis assumes pure-looking functions are pure.',
    rote: 'Copy-on-call fingerprinting: hash arguments at entry, re-hash at exit; any drift disqualifies the call.',
    why: 'A pure-by-inspection function can still mutate a list argument in place. Modern researchers passing DataFrames around hit this constantly.',
    citation: 'src/rote/purity.py · tests/unit/test_purity.py',
  },
  {
    topic: 'Coverage of pure long-running calls',
    paper: 'Reported high coverage on the original five-script corpus (fraction of pure calls memoized).',
    rote: '100% of cold compute eliminated on the warm re-run across corpus/realistic/ (five multi-second scripts, ~26 s → 0 s).',
    why: 'Different denominator — work eliminated vs. pure-call fraction. Flagging the mismatch rather than asserting parity.',
    citation: 'paper §4.3 · tests/integration/test_realistic_coverage.py',
  },
];
