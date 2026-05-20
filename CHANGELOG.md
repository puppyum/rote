# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Once a release tag exists the project will use
[Semantic Versioning](https://semver.org).

## [Unreleased]

### Fixed

- Cache correctness (P0): `rote.clear()` now wipes the per-wrapper
  in-memory tier as well as SQLite and blobs. The old behaviour left
  the in-memory entry intact, so the next call returned a stale hit
  without re-running the body.
- Cache correctness (P0): argument fingerprints include parameter names,
  so `f(1, 0)` and `f(0, 1)` produce distinct keys instead of colliding.
- Multi-process: SQLite `busy_timeout` is set before `journal_mode=WAL`.
  Concurrent opens of a fresh cache directory used to crash with
  *"database is locked"* before either process could promote the journal.
- File dependencies: `file_dep_hash()` no longer blocks indefinitely on
  FIFOs / non-regular files. Cache-dir sibling paths (`.rote-input.txt`
  next to `.rote/`) are no longer falsely excluded from dependency
  tracking by a naive `str.startswith` check.
- Purity: the static impure-callee set is recomputed when a function's
  globals change. Rebinding a helper to `time.time()` mid-session now
  invalidates previously-cached results that called the helper.

### Changed

- Bounded memory: five formerly-unbounded structures now have caps so
  long-running Jupyter kernels stay bounded:
  - `_PERF_BLACKLIST` — LRU, 4096 entries
  - `_Session.call_graph` — LRU, 8192 distinct callers
  - `PurityTracker.verdicts` — FIFO, 1024 entries
  - `Tracer.buffer` events drop `code` and `return_value` references
    after listener dispatch (only spill metadata is retained)
  - `Store._pending_hits` auto-flushes at 1024 entries
- Startup cost: PEP 578 audit hooks are deferred until the first
  `@cache` or `auto()` use. `import rote` no longer pays per-event hook
  dispatch on every file open or socket call.
- CI matrix: `windows-latest` joins the existing Linux + macOS jobs.

### Removed

- Dead helpers: `PurityTracker.attach_args`, `pop_verdict_for`,
  `note_close`; `compute_arg_fingerprints`, `mutable_arg_names`;
  `_CONTENT_HASH_LIMIT`; `identity.module_source_hash`; `Config.verbose`;
  `Config.install_import_hook`; `import_hook.uninstall`.

## [0.1.0]

Initial implementation. Four-layer architecture (tracing, identity,
serialization + store, purity) on PEP 669 `sys.monitoring` and PEP 578
audit hooks. Public API: `@rote.cache`, `rote.auto()`,
`rote.configure(...)`, `rote.stats()`, `rote.graph()`, plus the
`rote run / status / clear` CLI.
