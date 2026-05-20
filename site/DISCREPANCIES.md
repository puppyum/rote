# Paper vs. rote — what differs, and why

Every numeric or behavioural delta between the IncPy 2011 paper and the
contemporary `rote` reimplementation. Source for the dashboard's "What
changed" panel.

Numbers come from `bench/results/*.json` (regenerated 2026-05-19) and from
the project docs. Paper section references reflect the citation system the
project docs use throughout — the PDF could not be fetched in this build
environment; section claims are as recorded in `docs/WHATS_NEW.md` and
`docs/DECISIONS.md`.

## Headline edit-rerun speedup

| | Paper (2011) | rote (2026) | Why it differs |
|---|---|---|---|
| Edit-rerun on real workflows | ~10× | **4.9× cross-process** on a paper-shaped pipeline (`cross_process_pipeline.json`: 1.75 s plain → 0.35 s warm; joblib 0.19 s) | About half the paper's factor. Two reasons: hardware has moved (Apple Silicon NVMe is faster at the work the cache is skipping); and rote content-hashes file dependencies on every hit where the paper trusted `(size, mtime)`. The validation costs cycles but closes a stale-result hole. Joblib pays neither cost and is correspondingly faster here. |
| In-process variant | ~10× (paper §4.2 mixed in-process/cross-process) | **~48× in-process warm** on the same pipeline (`paper_pipeline.json`: 264 ms plain → 4.6 ms warm) | Upper bound once interpreter startup is amortized. Not the number that sits next to the paper's ~10× headline — the cross-process number is. |

## File-dependency tracking

| | Paper §3.5 | rote |
|---|---|---|
| Identity key | `(size, mtime)` | `(dev, ino)` indexed; `(size, mtime_ns, ctime_ns)` validated in a persistent SQLite table; content hash cached cross-process |
| `touch -r` mtime rewind after same-size overwrite | Would silently re-use the stale cache entry | Detected. `ctime_ns` moves anyway because the kernel updates ctime on every inode write and userspace cannot backdate it without root. Pinned by `tests/unit/test_file_hash_cache.py::test_size_preserving_mtime_backdated_edit_still_invalidates`. |
| Non-regular files (FIFO, device) | Unspecified | Conservative miss; never opened for content hashing (blocking concern). Matches the paper's "when uncertain, do not memoize" model. |

## Serialization (paper Figure 6)

The paper compared `pickle`, `cPickle`, and `marshal`. rote dispatches by
type because PyArrow IPC (2016), `numpy.save` (was present in 2011 but
underused for caching), and safetensors (2023) didn't exist in usable form
when the paper was written.

From `serialize_microbench.json` (write/read in ms, lower is better):

| Object | rote serializer | rote write | pickle write | rote read | pickle read |
|---|---|---|---|---|---|
| 1 M float64 numpy | numpy | 0.44 | 0.35 | 0.71 | 0.26 |
| 3 M float32 numpy | numpy | **0.66** | 1.12 | 0.89 | 0.56 |
| 1 M-row arrow table | arrow | **2.75** | 3.60 | 0.43 | 0.67 |
| 100K-item dict | msgpack | 46.5 | 10.9 | 45.5 | 17.4 |
| 1 M-int list | msgpack | 361.8 | 11.1 | 24.6 | 29.9 |

rote wins on the cases that matter to modern research (DataFrames,
ndarrays, tensors). Pickle still wins on huge homogeneous Python
containers; the README documents this as a known case to use Arrow for
instead. The point is the *dispatch*, not the per-format speed.

## Source-change detection

| Edit | Paper invalidates? | rote invalidates? | Why |
|---|---|---|---|
| Add a comment | Yes (false positive) | No | `libcst` canonical AST strips comments/docstrings/whitespace; blake3 of the canonical string is the function identity. libcst itself didn't exist when the paper was written. |
| Reformat with `ruff format` | Yes (false positive) | No | Same reason. |
| Rename a local consistently | Yes (false positive) | No | De Bruijn-style index rename on the canonical AST. |
| Add `: int` annotations | Yes | No | Annotations stripped before hashing. |
| Change a literal | Yes | Yes | (Both correct.) |
| Change an operator | Yes | Yes | (Both correct.) |
| Modify a transitive callee | Yes (via call-graph) | Yes (transitive function ids) | Same idea, different mechanism. |

## Compatibility / install

| | Paper IncPy | rote |
|---|---|---|
| Implementation | Patched CPython 2.6.3 | Stock CPython 3.12+ |
| Install | clone, configure, make | `pip install rote` |
| Tracking upstream CPython | Stopped years ago | Just upgrade the wheel |
| Why this is possible now | — | PEP 578 audit hooks (2018) and PEP 669 `sys.monitoring` (2023) let user code observe events the 2011 implementation needed an interpreter fork to see. |

## Coverage of pure long-running calls

| | Paper §4.3 | rote |
|---|---|---|
| Claim | High coverage on the original five-script corpus | **100% of cold compute eliminated** on the warm re-run across `corpus/realistic/` (five multi-second scripts, ~26 s → 0 s) |
| Denominator | Fraction of pure calls memoized | Fraction of compute eliminated on warm re-run |

These are different denominators and not directly comparable. Flagging the
mismatch rather than asserting parity.

## Concurrency

| | Paper | rote |
|---|---|---|
| Concurrent processes against one cache | Single-process design | SQLite WAL mode + atomic blob writes; 16-process hammer test in `tests/correctness/test_concurrency.py` (320 writes, no corruption) |

## Mutation detection

| Signal | Paper | rote |
|---|---|---|
| Argument mutated in place during the call | Not detected | Detected via copy-on-call fingerprint at entry, re-checked at exit |

## Coverage gaps where rote is narrower or gives up ground

- **Cross-process warm:** joblib stays about 2× ahead of rote because it
  skips file-content validation altogether. rote chose correctness; the
  cost is in the JSON above.
- **Sub-millisecond pipelines:** joblib's no-validation lookup beats
  rote's content-hashed lookup by a small absolute amount.
- **Million-element Python primitive containers:** pickle's C-level
  serialization wins; for that shape of data, prefer Arrow.
