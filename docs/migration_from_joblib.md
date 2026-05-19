# Migrating from `joblib.Memory` to `rote`

If you currently use `joblib.Memory`, switching to `rote` is one line in most
cases. If you use the *automatic* mode, it's zero lines.

## The 1-line port (decorator users)

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

## The 0-line port (you have many @mem.cache decorators)

Remove them all. Run your script under `rote run`:

```bash
rote run analyze.py
```

The tracer will catch every function that runs for ≥1 s and is pure. You
don't need decorators on individual functions — that's the whole point.

If you only want to enable it for part of your script:

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

## Behavioral differences to know

1. **rote refuses to cache impure calls.** `joblib` caches anything you
   decorate. If your function reads `os.environ` or calls `time.time()`,
   `joblib` cheerfully caches it, returning stale results forever. `rote`
   detects this and skips the cache write. You can see what was skipped via
   `rote.stats()["invalidation_reasons"]`.

2. **rote has a duration threshold.** By default, calls finishing in <1 s
   are not cached. The cost of writing and reading exceeds the cost of
   re-running them. Set `rote.configure(min_duration_s=0.0)` to disable
   the threshold, or `0.1` for a more aggressive policy.

3. **rote stores type info in the index.** When you fetch a cached
   `DataFrame`, you get back exactly a `DataFrame`, not a pickled object
   that happens to be a DataFrame after `__reduce__`. This makes cross-process
   sharing and offline inspection straightforward.

4. **rote source identity is canonical.** `joblib` re-runs your function
   if you change a comment. `rote` does not. Conversely, both correctly
   re-run if you change a literal or an operator.

## When `joblib` is still the right choice

* You're on Python ≤ 3.11. (`rote` requires 3.12 for `sys.monitoring`.)
* You need `mmap` support for very large NumPy arrays — `joblib`'s
  `mmap_mode` does this. `rote` does not yet; PyArrow IPC files can be
  mmap'd but the convenience wrapper is on our backlog.
* Your workload is bottlenecked on pickling million-element pure-Python
  containers (lists of millions of small ints). `pickle` is faster than
  `msgpack` for that specific shape. See `BENCHMARKS.md` for numbers.

In every other case the benchmarks ([`BENCHMARKS.md`](../BENCHMARKS.md))
show `rote` ≥ `joblib` warm-cache performance.
