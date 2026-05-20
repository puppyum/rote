# Migrating from `joblib.Memory` to `rote`

If you already use `joblib.Memory`, switching to `rote` is usually a one-line
change. If you use auto-mode, it's zero lines.

## One-line port: decorator users

Before:

```python
from joblib import Memory
mem = Memory(".cache", verbose=0)

@mem.cache
def expensive(df):
    return df.groupby("user").agg({"x": "sum"})
```

After:

```python
import rote

@rote.cache
def expensive(df):
    return df.groupby("user").agg({"x": "sum"})
```

That's it. `rote.cache` accepts and returns plain functions, just like
`joblib`. The cache lives in `.rote/` by default; override with
`rote.configure(cache_dir="/somewhere/else")`.

## Zero-line port: drop all your decorators

Strip the `@mem.cache` lines. Run the script through `rote run`:

```bash
rote run analyze.py
```

The tracer catches every function that runs for at least 1 s and is pure.
You don't need decorators on individual functions; that was the whole pitch
of the original IncPy paper.

To enable it only for part of a script:

```python
import rote

with rote.auto():
    expensive_pipeline(data)
# code outside the `with` block runs un-instrumented
```

## Feature comparison

| Feature | `joblib.Memory` | `rote` |
|---|---|---|
| Decorator API | `@mem.cache` | `@rote.cache` |
| Automatic (no-decorator) mode | No | **Yes** (`rote.auto()` or `rote run`) |
| Source-change invalidation | Whole-source hash | Canonical AST (cosmetic edits don't bust) |
| DataFrame storage | pickle | **PyArrow IPC** (smaller, faster, language-neutral) |
| NumPy storage | pickle | **numpy.save** |
| Mutation detection | None — caches even if your function mutates inputs | **Yes** (copy-on-call hashing) |
| I/O purity detection | None | **Yes** (audit hooks classify network/file/exec) |
| Time-saved instrumentation | None | `rote.stats()` |
| Concurrent multi-process cache | Yes | **Yes** (SQLite WAL + atomic blobs) |
| CLI | No | `rote run/status/clear` |
| Min Python version | 3.8 | 3.12 |

## Behavioural differences

1. rote refuses to cache impure calls. `joblib` caches whatever you
   decorate. If your function reads `os.environ` or calls `time.time()`,
   joblib happily returns stale results forever. rote detects the impurity
   and skips the cache write; the reason shows up in
   `rote.stats()["invalidation_reasons"]`.

2. rote has a duration threshold. Calls finishing in under 1 s aren't
   cached because the round-trip costs more than the call. Set
   `rote.configure(min_duration_s=0.0)` to disable the threshold, or
   `0.1` for something in between.

3. rote stores type info in the index. Fetching a cached `DataFrame`
   gives you back a `DataFrame`, not a pickled blob that happens to be a
   DataFrame after `__reduce__`. That makes cross-process sharing and
   offline inspection (open the Arrow IPC file directly) practical.

4. rote's source identity is canonical. Editing a comment doesn't bust
   the cache. Changing a literal or an operator does. joblib busts the
   cache on either.

## When joblib is still the right choice

* You're on Python ≤ 3.11. rote requires 3.12 for `sys.monitoring`.
* You need `mmap` support for very large NumPy arrays. joblib's
  `mmap_mode` covers this; rote doesn't yet. PyArrow IPC files can be
  mmap'd, but the convenience wrapper is on the backlog.
* Your workload is bottlenecked on pickling million-element pure-Python
  containers (a list of millions of small ints, say). `pickle` is faster
  than `msgpack` for that exact shape. Numbers in `BENCHMARKS.md`.

In every other case the benchmarks ([`BENCHMARKS.md`](./BENCHMARKS.md))
show `rote` ≥ `joblib` warm-cache performance.
