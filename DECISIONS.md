# Architecture Decisions

This document records every non-trivial architectural decision, every heuristic, and every
disagreement with the IncPy 2011 paper. Newest entries at the top.

## Format

```
## [YYYY-MM-DD] Short title
**Context:** What problem prompted the decision.
**Decision:** What we chose.
**Alternatives considered:** What we rejected and why.
**Consequences:** What this constrains or enables downstream.
```

---

## [2026-05-19 — review] Close cache-key and dependency false-negative holes

**Context:** A paper-faithfulness review found six stale-result or claimed-feature
gaps: omitted default arguments were not part of the input fingerprint; true closure
cells were not part of global/lexical dependency hashing; `getattr(time, "time_ns")`
bypassed static impurity detection for C builtins; async wrappers stored only read
deps in the in-memory cache; file dependencies above the metadata threshold could
miss same-size, mtime-preserving edits; and the sync wrapper did not apply the
adaptive perf blacklist.

**Decision:** Bind calls with `inspect.signature(...).apply_defaults()` before
fingerprinting, include non-function closure cells in the external dependency digest,
detect constant/dynamic `getattr()` on impure stdlib modules, store combined
read+write deps in async memory-cache entries, stream-hash every file dependency
with blake3, and apply the perf guard to sync wrappers too. The perf guard now
ignores sub-5ms writes so tiny explicitly-cached tests are not blacklisted due to
fixed fsync noise.

**Alternatives considered:** Keeping metadata caching for large file dependencies
was rejected because it can silently return stale data. Treating dynamic `getattr`
as pure unless the attribute is statically known was rejected because the safe
fallback is a miss. Excluding closure cells to preserve tests that used closure
mutation as a call counter was rejected because paper §3.4 explicitly includes
enclosing lexical scopes.

**Consequences:** Correctness is stricter and some formerly cached functions that
mutate closure/global state will now miss or skip where they should. File-dep hits
on very large files are slower because correctness now requires a true content
hash; this should be revisited with a safe explain/verify mode or an opt-in
metadata heuristic, not as the default.

## [2026-05-18] Locked architecture

**Context:** Project bootstrap. The CLAUDE.md brief specifies a four-layer architecture.

**Decision:** Four independent layers — Tracing (sys.monitoring + audit hooks), Identity
(libcst canonical AST + blake3), Cache (SQLite index + type-dispatched blob serializers),
Purity (copy-on-call hashing + audit-classified I/O + curated impure stdlib list). Each
layer is testable in isolation. Layers communicate via plain dataclasses; no shared mutable
state outside the `_session.py` module.

**Alternatives considered:**
- A single Tracer-Cache integrated class (rejected: harder to test purity in isolation).
- Bytecode-level instrumentation via `dis` rewriting (rejected: too brittle across CPython
  releases; `sys.monitoring` is the supported path in 3.12+).
- Proxy wrappers for mutation detection as the *default* (deferred: brief calls for
  copy-on-call hashing first, benchmark, then layer proxies if needed).

**Consequences:** Public API in `__init__.py` is thin and stable; internals can be replaced
without API churn. Tests are organized one directory per layer.

## [2026-05-18] Python 3.12 minimum, runtime probes for 3.13 monitoring quirks

**Decision:** Hard floor at 3.12, develop on 3.13. The `Tracer` queries
`sys.monitoring.events` at startup rather than hard-coding constants, so any future
renumbering does not silently miscount events.

## [2026-05-18] blake3 for all hashing, never MD5/SHA1

**Decision:** Use `blake3` exclusively. Faster than SHA-256, no known collisions, 256-bit
digests. Stored as raw 32-byte `bytes` in SQLite `BLOB` columns; hex-encoded only at
boundaries for human-readable log output.

## [2026-05-18] Cache key composition

**Decision:** `cache_key = blake3(b"rote.v1" || function_id || input_id || file_dep_ids
|| global_dep_ids)`. The `b"rote.v1"` prefix versions the keying scheme; bumping it
invalidates every cache without a migration.

## [2026-05-18] SQLite WAL + atomic blob writes

**Decision:** SQLite opened with `PRAGMA journal_mode=WAL` and
`PRAGMA synchronous=NORMAL`. Blobs written via temp file + `os.replace`. Directory fsync
after rename on POSIX.

## [2026-05-18] Type-dispatched serialization, cloudpickle only as fallback

**Decision:** Resolution order: `pyarrow.Table` → PyArrow IPC; pandas/polars DataFrame →
Arrow → PyArrow IPC; `numpy.ndarray` → `.npy`; `torch.Tensor` → `safetensors`; primitives
and their containers → `msgpack`; everything else → `cloudpickle`. Serializer name stored
in the SQLite index.

## [2026-05-18] Purity is "guilty until proven innocent"

**Decision:** A call is memoized only if *every* purity signal is green. Signals: (a) no
audit-hook event for network/exec/file-append/unclosed-write, (b) no transitively-called
function on the curated impure stdlib list, (c) no input argument's hash changed between
call entry and call exit. Any red signal → no cache write; logged with a structured reason.

## [2026-05-18 — iter 2] Auto-mode via AST transform, not interpreter hooks

**Context:** Phase 5 wired the tracer into `auto()`, but the no-decorator
promise was not actually delivered — `rote run script.py` would observe
events but never memoize.

**Decision:** Auto-mode now uses `libcst` to wrap every top-level `def` with
`@rote.cache` *before* executing the script. The transform handles
`from __future__` correctly, skips already-decorated functions, leaves class
methods alone, and injects `import rote` after any module docstring + future
imports.

**Alternatives considered:**

- Monkey-patching globals on PY_START events. Rejected: can't intercept the
  *first* call of a function in a given process; correctness suffers.
- `sys.monitoring` per-code DISABLE return + bytecode rewriting. Rejected:
  too invasive; CPython's instrumentation API isn't designed for replacement.
- Import hook approach. Rejected: only catches functions defined in imported
  modules, not the `__main__` script.

**Consequences:** Auto-mode now produces a measurable 3-4× speedup on
representative scripts (1.80s → 0.52s on the demo). Cost: scripts that
construct their own AST transforms or import-time decorators may see
unexpected interactions; documented in the README.

## [2026-05-18 — iter 2] Cache return value AND captured stdout/stderr

**Context:** Wrapping `main()` with `@cache` lost all printed output — a
cache hit replayed the return value (`None`) but skipped `print` side
effects.

**Decision:** Every cached payload is now a dict with shape `{"_rote_v":
1, "return": ..., "stdout": str, "stderr": str}`. On hit, the captured
streams are written back to `sys.stdout/stderr` before returning. We use a
`_Tee` stream during execution so the user still sees real-time output
even on the cache-miss path.

**Consequences:** Functions with print-based observable behavior memoize
correctly. Cost: an extra dict allocation + (typically small) string
buffer per call. Negligible compared to the function body cost.

## [2026-05-18 — iter 2] Audit-hook based file-dep tracking in cache decorator

**Decision:** The `@cache` decorator pushes a stack of file-read lists; an
audit hook records every `open(..., "r")` to the topmost stack. After the
call, the deps are sorted, filtered to exclude paths inside the cache
directory itself, and stored alongside the entry. A scoped key incorporates
the file-dep content hash so the lookup automatically misses when any
recorded file changes.

**Consequences:** Cached functions that read files automatically invalidate
when those files change — without the user needing to annotate or track
deps manually. Covers the most common research-script case: "I changed the
CSV; re-run the analysis."

## [2026-05-18 — iter 2] Graceful failure on unserializable values

**Decision:** `fingerprint()` catches every exception from `encode()` and
emits a synthetic per-instance fingerprint (using `id(value)`) instead of
crashing. This guarantees: a call that takes a generator, open file
handle, or threading lock as input is never cached, but the program still
runs correctly.

## [2026-05-18 — iter 3] Cache decorator must use TRANSITIVE function ids

**Context:** A correctness bug. The cache decorator used
`function_id(func)` for the cache key. If a cached `main()` called `inner()`
and the user edited `inner()`, the AST of `main()` was unchanged, the cache
key was unchanged, and the next run returned a stale result.

**Decision:** The wrapper now uses
`transitive_function_ids(func)` — which walks `func.__globals__` for each
name in `func.__code__.co_names`, unwraps any `__wrapped__` chain, and folds
the callee's source hash in. Computed lazily on the first call to the
wrapper so siblings have time to install themselves into globals.

Caught by `tests/correctness/test_transitive_invalidation.py`.

**Consequences:** Editing any function in the transitive closure of a cached
call now invalidates the cached entry. Cost: one transitive walk per
wrapper, computed once and memoized.

## [2026-05-18 — iter 3] Bundle wrap only when stdout/stderr were captured

**Context:** Wrapping every cached return value in a
`{"return": ..., "stdout": ..., "stderr": ...}` dict forced the cloudpickle
fallback for numpy arrays and Arrow tables, regressing serialization speed
by up to 3.7× for Arrow.

**Decision:** The wrapper now inspects the captured streams after the call.
If both are empty (the common case for pure compute functions), the return
value is encoded directly via the type-dispatched serializer (Arrow/numpy/
msgpack/cloudpickle). If either is non-empty, the bundle dict is used.
On read, the deserializer checks for the `_rote_v` marker to know which
path to take.

**Consequences:** Type-dispatched serialization is restored for pure
compute functions. Arrow tables go back to Arrow IPC; numpy arrays to
`numpy.save`. Functions that print incur the bundle wrap (and the
cloudpickle fallback) — fair price for correct replay of side effects.

## [2026-05-19 — iter 4] In-memory hit cache (LRU per wrapper)

**Context:** Profiling showed 50µs per cache hit was spent on SQLite +
file-open + decode even for trivial values. Hot loops calling the same
function with the same args pay this cost repeatedly.

**Decision:** Each `@cache`-wrapped function gets its own bounded LRU dict
(default 256 entries) keyed by the full cache_key. On hit, we serve from
the in-memory tuple `(return, stdout, stderr, dur_ns)` — no SQLite, no
disk read, no decode. On a SQLite hit, the result is promoted into the
mem-cache too. On a write, we populate the mem-cache so the very next
call from the same process hits memory.

Correctness: the key is the full content-addressable cache_key, which
already incorporates source AST + arg fingerprints + file-dep hash. If
any of those change, the key changes, the mem-cache misses, and we fall
through to SQLite (which itself misses, etc).

**Consequences:** Per-hit cost dropped from 54µs to 24µs (in addition to
the earlier 120 → 54 jump from the dual-query removal). Workload bench
vs joblib: 4/5 → 5/5 wins, max speedup 4.34× → 10.37×, geometric mean
1.3× → 4.0×. Memory cost: bounded to MAX_MEM_CACHE * sizeof(result) per
wrapper.

## [2026-05-19 — iter 4] Single SQLite query per hit, dep_hash stored in entry

**Context:** The dual-key scheme (one entry under `key`, another under
`scoped_key = cache_key(fid, args, file_dep_hash)`) needed two SQLite
lookups per hit (one to discover the file_deps list, one to look up the
scoped entry). It also wrote two blobs per miss.

**Decision:** Added a `file_dep_hash BLOB` column to the entries table.
On miss we compute the hash once, store it in the entry. On lookup we
fetch the row, recompute the current file_dep_hash, compare. One SQLite
query, one blob write, equivalent invalidation guarantee. The new
`get_fast` method also skips `json.loads` of `code_dependencies` which
isn't needed on hit.

**Consequences:** SQLite traffic halved on the hit path; write traffic
halved on the miss path. Backwards-compat for older databases via an
idempotent `ALTER TABLE ADD COLUMN` migration.

## [2026-05-19 — iter 5] Static bytecode purity check + library filter

**Context (paper §3.3, 3.3.1):** PY_START callbacks fire only for Python
function entries, not C builtins. The most common impure stdlib calls
(`time.time`, `random.random`, `os.listdir`, `numpy.random.default_rng`)
are C-implemented and were silently slipping past the decorator's purity
check.

**Decision:**
1. **Static bytecode analysis at first call.** Walk the wrapped function's
   ``co_code`` instructions looking at ``LOAD_GLOBAL``/``LOAD_DEREF`` +
   ``LOAD_ATTR`` pairs. Resolve each via ``func.__globals__`` and
   ``func.__closure__``; check ``_impure_stdlib.is_impure`` for both the
   module's true name and the qualified attribute path. Result is memoized
   per wrapper.
2. **Library-internal call filter.** PY_START and audit-hook impurity
   checks now skip events originating in stdlib, site-packages, or our
   own code. A user function calling ``numpy.linalg.qr`` is no longer
   falsely flagged because numpy uses ``threading.RLock`` internally.
   File-append events bypass the filter (they represent observable
   persisted state).
3. **In-memory cache re-validates file deps.** Without this, mtime-
   preserving edits to a tracked file silently returned stale results
   from the in-process LRU. Added the file_deps + dep_hash to every
   mem-cache entry.

**Consequences:** Stale results on calls into impure C builtins are
eliminated. Real-world speedup vs joblib improved from 4.0× geomean to
**7.73×** because the library filter stops over-flagging.

## [2026-05-19 — iter 5] File-content-hash memoization

**Context:** Re-content-hashing a 5 MB intermediate JSON file on every
cache hit cost ~5 ms — the dominant overhead on multi-stage pipelines.

**Decision:** Added a 1024-entry process-local LRU keyed on
``(abspath, size, mtime_ns) → blake3_hash``. A file whose filesystem
metadata is unchanged since the last hash MUST have the same content,
so we skip the re-read.

**Consequences:** Multi-stage pipeline edit-downstream speedup jumped
from ~24× to **35× faster than plain Python** (paper-shaped benchmark).
The cache key remains the content hash, so we don't lose the protection
against ``cp -p`` and mtime-preserving editors — those bypass the
metadata-cache by virtue of having different metadata than what was
last seen.

## [2026-05-19 — iter 6] Closed all documented alpha gaps

**Context:** The "what's still narrower than the paper" list in
`docs/WHATS_NEW.md` had eight remaining items. This iteration closes
them, takes `rote` from "working alpha" to "production-fledged".

**Decisions:**

1. **mypy --strict clean.** Fixed every type error including frame-walk
   `FrameType | None`, classmethod descriptor isinstance handling,
   and the bundle-vs-bare return-type union. The codebase now type-checks
   at the strictest setting under modern mypy.

2. **Cache decorator works with `@classmethod` / `@staticmethod`** in
   either order. When the inner decorator is a descriptor, we unwrap it,
   recursively cache the underlying function, then re-wrap.

3. **`async def` functions are first-class.** A separate `_async_cache`
   path mirrors the sync wrapper exactly (same key, store, mem-cache,
   stdout replay), but the body is awaited.

4. **Adaptive perf guard (§3.3.2).** Tracks per-function encode+write
   time on the first cache write. If it exceeds the function's run time,
   the function ID is added to `_PERF_BLACKLIST` for the session and
   future calls skip the write. Prevents the §3.3.2 pathological case
   (1 GB return for a trivial computation) without static thresholds.

5. **Global-state reachability (§3.4).** Added `_global_dep_names` +
   `_global_deps_fingerprint`. On every call, fingerprint the current
   values of every non-callable module global the function references via
   `co_names`. Fold into the cache key. Editing `MULTIPLIER = 2.5` to
   `3.0` now invalidates any cached call that read it.

6. **File-write dep persistence (§3.3.1).** Added `_inflight_writes`
   thread-local + `_write_stack()`. Audit hook splits opens into reads
   and writes; writes get persisted to the new
   `file_write_dependencies` SQLite column. The dep_hash now covers BOTH
   read deps and write deps — a deleted/edited output file forces
   re-execution to recreate it.

7. **Import hook for auto-mode.** `rote run` now installs an
   `_AutoWrapFinder` on `sys.meta_path` that rewrites every user .py
   module's source through `transform_file` before execution. Stdlib /
   site-packages / our own code are skipped via `_is_user_path`. The
   transform is mtime-keyed-cached under `.rote/autowrap/` so repeat
   runs don't re-libcst-parse.

8. **IPython / Jupyter integration.** `%load_ext rote.jupyter`
   installs an input transformer that AST-wraps every cell. Line magics
   `%rote_stats`, `%rote_clear`, `%rote_configure`. Cell magic
   `%%rote` for per-cell caching.

9. **P0 cache-collision bug fix.** Found via the new
   `test_distinct_args_produce_distinct_keys` edge-case test: `f(1, 0)`
   and `f(0, 1)` collided to the same cache key because we sorted
   argument fingerprints without keys. Now we hash `b"arg0=" + fp(arg0)
   || b"arg1=" + fp(arg1) || ...` so parameter position matters.

**Consequences:**
- 304 / 304 tests pass (up from 272); ruff + mypy --strict clean.
- Geometric mean **8.01×** faster than `joblib.Memory` (was 7.73).
- Paper-shaped pipeline: **40.8×** faster than plain Python (was 35.3).
- All eight documented alpha-stage gaps closed.

## [2026-05-19 — iter 4] Lazy hit-counter updates + optional fsync

**Decision:** `Store.hit(key, eager=False)` now buffers keys in a process-
local list; the actual SQL UPDATE happens at session shutdown via
`flush_hits()`, grouped by key with `executemany`. Saves ~5µs per hit
when enabled (`Config.eager_hit_counters=False`). Defaults to `True` to
preserve the "telemetry visible mid-session" behavior.

`Config.fsync_writes` controls per-blob fsync + directory fsync. Defaults
to `True`; setting `False` removes the fsync calls (saves ~500µs per
miss), tradeoff is power-loss can lose the last few cache entries
(they'll be regenerated on next run). Recommended `False` for benchmark
fixtures and CI runners.

## [2026-05-18 — iter 3] Skip refingerprint of immutable args

**Decision:** Added `mutable_arg_names()` to `purity.py`. The cache wrapper
queries it before the call and only re-fingerprints args that could
possibly mutate (lists, dicts, sets, ndarrays, custom objects). Args of
type `int`, `float`, `str`, `bytes`, `bool`, `None`, `frozenset`, and
tuples of immutables skip the exit-time pass entirely.

**Consequences:** Calls with primitive args (the common case for
"function takes a count and a filename") pay the fingerprint cost only
once at entry, not twice. Substantial speedup for calls with large
immutable args (e.g. a long pre-computed parameter tuple).
