"""Curated list of stdlib symbols that make a function impure.

Each entry is a fully-qualified dotted path or a (module, prefix) pair. A call
to any symbol matching the list during a tracked function's execution marks the
function impure.

Entries are exhaustive within each module: if ``time.time`` is impure, the
whole ``time`` module is treated impure unless individually exempted.

Sources of impurity: clock reads, randomness without seed argument, environment
reads, network, subprocess, user I/O, anything that mutates module-level state
visible across calls.
"""

from __future__ import annotations

# Modules treated as wholly impure (every callable inside).
IMPURE_MODULES: frozenset[str] = frozenset(
    {
        # Clocks and timing
        "time",
        # Network and subprocess
        "socket",
        "ssl",
        "subprocess",
        "asyncio.subprocess",
        "urllib",
        "urllib.request",
        "urllib.error",
        "http.client",
        "http.server",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        # Third-party HTTP clients commonly used in research scripts
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        # Process and env
        "signal",
        "multiprocessing.connection",
        # Threading primitives — they typically signal coordination, treat impure.
        "threading",
        # File-system mutation
        "shutil",
        "tempfile",
        # Filesystem snapshot reads (treat as impure: directory state can change
        # between cache miss and hit, but we don't track it as a content dep).
        "glob",
        # Logging — usually writes timestamps and is shared global state.
        "logging",
        # Cryptographic randomness — explicit non-determinism.
        "secrets",
        # Operating system level
        "fcntl",
        "termios",
        "pty",
        "select",
        # GUI / sound
        "tkinter",
        "winsound",
    }
)

# Specific symbols that are impure even though the module is not entirely so.
IMPURE_SYMBOLS: frozenset[str] = frozenset(
    {
        # random.* is impure unless an explicit seed is passed BEFORE the call;
        # we err on the safe side and mark all of it impure. Users who want
        # seeded reproducibility should pass a `random.Random(seed)` instance
        # and we'll see them allocating it — that's fine.
        "random.random",
        "random.randint",
        "random.choice",
        "random.choices",
        "random.uniform",
        "random.gauss",
        "random.shuffle",
        "random.sample",
        "random.seed",  # mutates global state
        # os.environ access (reads), CWD, login, etc.
        "os.environ",
        "os.getenv",
        "os.getcwd",
        "os.chdir",
        "os.getpid",
        "os.getlogin",
        "os.umask",
        "os.unlink",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.removedirs",
        "os.mkdir",
        "os.makedirs",
        "os.rmdir",
        "os.system",
        "os.popen",
        "os.exec",
        "os.urandom",
        # Standard input
        "builtins.input",
        "sys.stdin",
        # UUIDs are non-deterministic
        "uuid.uuid1",
        "uuid.uuid4",
        # Date / time pulls
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        # pathlib filesystem snapshot reads — directory listings can change.
        "pathlib.Path.glob",
        "pathlib.Path.rglob",
        "pathlib.Path.iterdir",
        "pathlib.Path.stat",
        "pathlib.Path.lstat",
        # Filesystem snapshot reads — directory listings can change between
        # cache miss and the next hit; we don't track directory state as a dep.
        "os.listdir",
        "os.walk",
        "os.scandir",
        "os.stat",
        "os.lstat",
        "os.path.getmtime",
        "os.path.getctime",
        "os.path.getatime",
        "os.path.getsize",
        "os.path.exists",
        "os.path.isfile",
        "os.path.isdir",
        # Non-seeded random sources from numpy/torch — explicit non-determinism
        # at runtime. Users wanting reproducibility should pass a Generator.
        "numpy.random.random",
        "numpy.random.rand",
        "numpy.random.randn",
        "numpy.random.normal",
        "numpy.random.randint",
        "numpy.random.choice",
        "numpy.random.shuffle",
        "torch.rand",
        "torch.randn",
        "torch.randint",
        "torch.randperm",
    }
)

# Whitelist: even if module is in IMPURE_MODULES, these names are fine.
SAFE_OVERRIDES: frozenset[str] = frozenset(
    {
        "time.struct_time",  # pure dataclass
        "time.gmtime",  # pure transformation of an input
        "time.localtime",  # ditto, modulo TZ env
        "time.strftime",  # formatting
        "time.strptime",  # parsing
        "time.mktime",  # arithmetic
    }
)


def is_impure(qualified_name: str) -> bool:
    """Return True iff ``qualified_name`` should be considered impure."""
    if qualified_name in SAFE_OVERRIDES:
        return False
    if qualified_name in IMPURE_SYMBOLS:
        return True
    head = qualified_name.split(".", 1)[0]
    return head in IMPURE_MODULES
