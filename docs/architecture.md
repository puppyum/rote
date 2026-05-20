# Architecture

`rote` is structured as four independent layers, plus a thin session façade.
Each layer is testable in isolation and has its own test directory.

```
┌─────────────────────────────────────────────────┐
│ Layer 5 — session.py                             │
│   public API (cache, auto, invalidate, stats)   │
│   wires L1-L4, handles configuration           │
└────────────────────┬────────────────────────────┘
                     │
       ┌─────────────┼─────────────┬─────────────┐
       ▼             ▼             ▼             ▼
┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐
│ L1: trace│  │ L2: ident│  │ L3: cache │  │ L4: purity│
│sys.monit │  │libcst+   │  │SQLite +   │  │copy-on-   │
│audit hook│  │blake3    │  │type-disp. │  │call hash  │
└──────────┘  └──────────┘  └───────────┘  └──────────┘
```

## Layer 1: tracing (`trace.py`)

The tracer is a passive event source. It doesn't decide what to cache; it
emits structured `TraceEvent` dataclasses for downstream layers to consume.

* `sys.monitoring` (PEP 669, available 3.12+) for `PY_START`, `PY_RETURN`,
  `PY_RESUME`, `PY_YIELD`, `PY_UNWIND`, `RAISE`. Event ids are queried at
  startup rather than hard-coded, so future CPython renumbering is safe.
* `sys.addaudithook` (PEP 578) for `open`, `socket.*`, `urllib.*`,
  `http.client.*`, `exec`, `compile`, `import`. Audit-hook callbacks
  classify events conservatively: when in doubt, mark impure.
* Events flow into a bounded `deque`. Overflow spills the oldest events to
  `<cache_dir>/trace_spill.jsonl`.

`sys.monitoring` is the supported way to instrument Python in 3.12+. It's
faster than `sys.settrace`, doesn't need an interpreter fork, and works
with CPython's adaptive interpreter.

## Layer 2: identity (`identity.py`)

Function identity is the content hash of the canonical AST of the source.

* `libcst` parses source, strips comments, docstrings, type annotations,
  and blank lines, then renames all bound variables to De Bruijn-style
  indices (`_v0`, `_v1`, …), emits a stable canonical string, and `blake3`
  hashes it.
* Function-id is memoized on the `code` object's identity.
* Cache key = `blake3(b"rote.v1" || function_id || input_id ||
  file_dep_ids || global_dep_ids)`. Bumping the `v1` prefix invalidates
  the entire cache without a migration.

Two functions with the same observable behaviour should hash to the same
identity, regardless of cosmetic edits. The De Bruijn rename means
`def f(x): return x + 1` and `def f(name): return name + 1` hash the same.
Adding a `# comment` doesn't bust the cache; changing a literal does.

The harness in `tests/property/test_identity_properties.py` exercises
both sides — invariance (same hash) and sensitivity (different hash) —
across hundreds of generated examples.

## Layer 3: cache

### 3a. Serialization (`serialize.py`)

Type-dispatched. Resolution order (first match wins):

| Predicate | Serializer | Stored format |
|---|---|---|
| `pyarrow.Table` | `arrow` | PyArrow IPC stream |
| `pandas.DataFrame` | `pandas` | via Arrow IPC |
| `polars.DataFrame` | `polars` | via Arrow IPC |
| `numpy.ndarray` | `numpy` | `numpy.save` |
| `torch.Tensor` | `torch-safetensors` | `safetensors` |
| msgpack-able primitives + containers | `msgpack` | msgpack with `use_bin_type=True` |
| everything else | `cloudpickle` | cloudpickle protocol 5 |

The chosen serializer name is recorded in the SQLite row so deserialization
doesn't need to sniff.

### 3b. Store (`store.py`)

* `<cache_dir>/index.db` — SQLite (WAL mode, `synchronous=NORMAL`).
* `<cache_dir>/blobs/<first-2-hex>/<rest>.bin` — payload files.

Atomicity: every blob write goes to a tempfile in the same directory, is
fsync'd, then `os.replace`-renamed. On POSIX, the parent directory is
also fsync'd. Concurrent writes from many processes are coordinated by
SQLite's own locking (`busy_timeout=10s`, set before `journal_mode=WAL`
so the WAL pragma doesn't crash a contending process).

The concurrency harness (`tests/correctness/test_concurrency.py`) hammers
a single store with 16 spawned processes for 320 writes total and
verifies no corruption.

## Layer 4: purity (`purity.py`)

`PurityTracker` attaches to the tracer as a listener and maintains a per-call
frame stack in parallel with Python's actual frame stack. For each completed
call, it emits a `Verdict(pure: bool, reasons: [...], duration_ns, file_deps)`.

Three independent purity signals:

1. Audit-hook classification. Network access marks the call and every
   ancestor impure. File `"a"` mode open is impure. File `"w"` mode left
   open at function exit is impure. File `"w"` mode opened and closed
   inside the same function is pure with a write dependency
   (paper §3.3.1).
2. Curated impure stdlib list (`_impure_stdlib.py`). `time.*`,
   `random.*`, `os.environ`, `subprocess.*`, `socket.*`, `uuid.uuid1/4`,
   `datetime.now/today`, `input()`, `sys.stdin`, and so on. A walk through
   any of these marks the entire call stack impure.
3. Copy-on-call hashing. Argument content fingerprints are recomputed at
   call exit; any change means the function mutated its inputs and is no
   longer referentially transparent, so it gets skipped.

Decision rule: any red signal blocks the cache write. A structured reason
is recorded in `stats()["invalidation_reasons"]`. Missed memoization is
fine; stale results are not.

## Layer 5: session (`session.py`)

The public API. Holds the singleton `_session` state — current tracer (if
any), current purity tracker, the open `Store`, in-memory `SessionStats`, and
the observed call graph. `auto()` enters a context where the tracer is
running; `rote run` also AST-wraps the entry script and installs the import
hook so imported user modules get wrapped too.

Telemetry: `stats()` returns hit/miss counters, `saved_seconds` (sum of
recorded durations × hit count), and a breakdown of why misses occurred. When
`telemetry=True` (default), each `auto()` block flushes a JSON snapshot to
`<cache_dir>/sessions/<unix_ts>.json` for offline analysis.

## CLI (`cli.py`)

```
rote [--cache-dir DIR] run SCRIPT [args...]
rote [--cache-dir DIR] status
rote [--cache-dir DIR] clear
```

`run` transforms the entry script with `autowrap.transform_file`, installs the
optional import hook, executes the compiled transformed source inside an
`auto()` block, and prepends the script's parent to `sys.path`. With
`--verbose`, `stats()` is dumped to stderr.

## Trade-offs

* Tracer overhead on call-heavy code that doesn't memoize. PEP 669
  callbacks are Python-level. On scripts with millions of short calls
  and nothing to cache, expect 10–30% overhead. The `min_duration_s`
  threshold exists for this case.
* First-call overhead for cache writes. Serializing a 100 MB DataFrame
  to Arrow IPC takes around 30 ms. The second run is free.
* Imperfect call-graph attribution in `auto()` mode. Edges are recorded
  between the topmost two Python frames on every CALL; this misses some
  edges across `eval`/`exec` boundaries. Fine for visualization, not
  load-bearing for invalidation.
