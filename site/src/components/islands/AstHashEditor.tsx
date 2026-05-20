import { useEffect, useMemo, useRef, useState } from 'react';
import { canonicalSource, hash } from '../../lib/canonical';

const DEFAULT_SRC = `def build_features(df: pd.DataFrame, weight: float = 1.0) -> pd.DataFrame:
    """Compute weighted log-features for the training set."""
    # cosmetic comment — should NOT change the hash
    out = df.copy()
    out["log_x"] = (out["x"] + 1).map(math.log) * weight
    return out
`;

const PRESETS: { label: string; desc: string; src: string }[] = [
  {
    label: 'baseline',
    desc: 'Reset to the original function.',
    src: DEFAULT_SRC,
  },
  {
    label: 'add a comment',
    desc: 'Cosmetic edit. Paper invalidates; rote does not.',
    src: `def build_features(df: pd.DataFrame, weight: float = 1.0) -> pd.DataFrame:
    """Compute weighted log-features for the training set."""
    # cosmetic comment — should NOT change the hash
    # second cosmetic comment added on a whim
    out = df.copy()
    out["log_x"] = (out["x"] + 1).map(math.log) * weight
    return out
`,
  },
  {
    label: 'rename a local',
    desc: 'Consistent rename — same observable behaviour.',
    src: `def build_features(df: pd.DataFrame, weight: float = 1.0) -> pd.DataFrame:
    """Compute weighted log-features for the training set."""
    frame = df.copy()
    frame["log_x"] = (frame["x"] + 1).map(math.log) * weight
    return frame
`,
  },
  {
    label: 'change a literal',
    desc: 'Semantic edit. The +1 becomes +2. Hash changes.',
    src: `def build_features(df: pd.DataFrame, weight: float = 1.0) -> pd.DataFrame:
    """Compute weighted log-features for the training set."""
    out = df.copy()
    out["log_x"] = (out["x"] + 2).map(math.log) * weight
    return out
`,
  },
];

/**
 * Live AST-hash editor.
 *
 * Demonstrates the property in `src/rote/identity.py`: comment / format /
 * annotation / consistent-rename edits leave the canonical hash unchanged;
 * literal / operator / call-target edits change it.
 *
 * Implementation note (read-me from the dashboard's NOTES.md too):
 * the real canonical hash lives in libcst Python with blake3. This widget
 * approximates it with a JS string transform plus SHA-256 so the page is
 * interactive on first paint without the 6+ MB cost of loading Pyodide.
 * The four preset edits exercise the property exactly the way the Python
 * implementation does; the textarea is freeform for exploration.
 */
export default function AstHashEditor() {
  const [src, setSrc] = useState(DEFAULT_SRC);
  const [digest, setDigest] = useState<string>('…');
  const [baselineDigest, setBaselineDigest] = useState<string>('…');
  const [canonical, setCanonical] = useState<string>('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Compute baseline hash on mount.
  useEffect(() => {
    void hash(canonicalSource(DEFAULT_SRC)).then(setBaselineDigest);
  }, []);

  // Live-update the displayed hash whenever src changes.
  useEffect(() => {
    const canonicalStr = canonicalSource(src);
    setCanonical(canonicalStr);
    let cancelled = false;
    void hash(canonicalStr).then((d) => {
      if (!cancelled) setDigest(d);
    });
    return () => {
      cancelled = true;
    };
  }, [src]);

  const matchesBaseline = digest === baselineDigest && digest !== '…';
  const shortDigest = digest === '…' ? '…' : `${digest.slice(0, 12)}…${digest.slice(-6)}`;

  return (
    <section id="try" className="container-wide mt-24 scroll-mt-24" aria-labelledby="try-h">
      <header className="mb-8 max-w-3xl">
        <p className="cite">08 · try it yourself</p>
        <h2 id="try-h" className="mt-1 text-3xl font-semibold leading-tight">
          The canonical AST hash, in the browser.
        </h2>
        <p className="mt-3 text-base text-[var(--color-ink-soft)]">
          Edit the function. Watch the hash. Adding a comment or renaming a local leaves it
          steady: same observable behaviour, same identity. Changing a literal or an operator
          changes it. The paper §3.2 description was coarse source-byte hashing; this is what
          canonical-AST hashing looks like with <code>libcst</code> available.
        </p>
        <p className="mt-2 text-xs text-[var(--color-ink-faint)]">
          This widget runs a JS approximation of the same canonicalisation that
          <code> src/rote/identity.py </code>does — comments, docstrings, annotations stripped;
          parameters indexed; SHA-256 stands in for blake3 because Web Crypto has the former
          natively. The Python implementation is the source of truth.
        </p>
      </header>

      <div className="grid gap-4 rounded-md border hairline bg-white/40 p-4 sm:p-6 md:grid-cols-[3fr_2fr]">
        <div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <label className="cite" htmlFor="ast-editor">
              source
            </label>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => {
                    setSrc(p.src);
                    if (textareaRef.current) textareaRef.current.focus();
                  }}
                  className="pill hover:bg-white/70"
                  title={p.desc}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <textarea
            ref={textareaRef}
            id="ast-editor"
            value={src}
            onChange={(e) => setSrc(e.target.value)}
            spellCheck={false}
            rows={12}
            className="mt-2 w-full resize-y rounded-sm border hairline bg-[#fbf8f1] p-3 font-mono text-[13px] leading-snug text-[var(--color-ink)] outline-none focus:border-[var(--color-rote)]"
          />
        </div>
        <aside className="flex flex-col gap-3">
          <div>
            <p className="cite">canonical hash</p>
            <p
              className={`mt-1 font-mono text-base ${
                matchesBaseline ? 'text-[var(--color-rote)]' : 'text-[var(--color-warn)]'
              }`}
              aria-live="polite"
            >
              {shortDigest}
            </p>
            <p className="mt-1 text-sm text-[var(--color-ink-soft)]">
              {matchesBaseline
                ? 'Same as baseline. The cache would hit.'
                : digest === '…'
                  ? 'Hashing…'
                  : 'Different from baseline. The cache would miss.'}
            </p>
          </div>
          <div>
            <p className="cite">baseline hash</p>
            <p className="mt-1 font-mono text-xs text-[var(--color-ink-faint)]">
              {baselineDigest === '…' ? '…' : `${baselineDigest.slice(0, 12)}…${baselineDigest.slice(-6)}`}
            </p>
          </div>
          <details className="mt-1">
            <summary className="cite cursor-pointer">show canonical source</summary>
            <pre className="mt-2 max-h-48 overflow-auto rounded-sm border hairline-soft bg-[#fbf8f1] p-3 font-mono text-[11px] leading-snug text-[var(--color-ink-soft)] whitespace-pre">
{canonical}
            </pre>
          </details>
        </aside>
      </div>

      <FileDepInset />
    </section>
  );
}

interface FileDepRow {
  size: number;
  mtimeNs: bigint;
  ctimeNs: bigint;
  contentHash: string;
  cacheHits: boolean;
}

/**
 * Optional sibling — the file-dep adversarial edit. Three buttons:
 *
 *  - normal edit     : size + mtime + ctime + content_hash all change → miss
 *  - edit + touch    : content changes, mtime kept artificially old, but ctime
 *                      still moves because the kernel updates it on every
 *                      inode write. content_hash mismatches → miss.
 *  - touch -r rewind : same-size, mtime backdated to the original. Still
 *                      caught: ctime_ns is the kernel-managed signal that
 *                      userspace can't fake without root.
 *
 * The point is that ctime_ns + content_hash are the two extra signals rote
 * adds over paper §3.5's (size, mtime).
 */
function FileDepInset() {
  type Scenario = 'baseline' | 'normal' | 'touchPreserveMtime' | 'rewindMtime';
  const [scenario, setScenario] = useState<Scenario>('baseline');

  const baseline: FileDepRow = {
    size: 4096,
    mtimeNs: 1716080400000000000n,
    ctimeNs: 1716080400000000000n,
    contentHash: '7f3a91bd4e2c8f4a',
    cacheHits: true,
  };

  const rows = useMemo<Record<Scenario, FileDepRow>>(
    () => ({
      baseline,
      normal: {
        size: 4123,
        mtimeNs: 1716166800000000000n,
        ctimeNs: 1716166800000000000n,
        contentHash: '11ce40a8d22b6dfa',
        cacheHits: false,
      },
      touchPreserveMtime: {
        size: 4096,
        mtimeNs: baseline.mtimeNs,
        ctimeNs: 1716166800000000000n,
        contentHash: '9ba2715fcd0a4818',
        cacheHits: false,
      },
      rewindMtime: {
        size: 4096,
        mtimeNs: baseline.mtimeNs,
        ctimeNs: 1716166800000000000n,
        contentHash: '03cf882ab7e95110',
        cacheHits: false,
      },
    }),
    [],
  );

  const current = rows[scenario];

  return (
    <div className="mt-6 rounded-md border hairline-soft bg-white/30 p-4 sm:p-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3">
        <div>
          <p className="cite">08b · the file-dependency adversarial edit</p>
          <p className="mt-1 text-sm text-[var(--color-ink-soft)]">
            Paper §3.5 keyed file deps on <code>(size, mtime)</code>. rote also tracks{' '}
            <code>ctime_ns</code> and a content hash. Toggle the scenarios to see which signal
            catches each edit.
          </p>
        </div>
        <a
          href="https://github.com/puppyum/rote/blob/main/tests/unit/test_file_hash_cache.py"
          className="cite text-[var(--color-rote)] hover:underline"
        >
          test_file_hash_cache.py
        </a>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        {(
          [
            ['baseline', 'baseline'],
            ['normal', 'normal edit'],
            ['touchPreserveMtime', 'edit + touch (preserve mtime)'],
            ['rewindMtime', 'touch -r (rewind mtime)'],
          ] as [Scenario, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setScenario(key)}
            aria-pressed={scenario === key}
            className={`pill ${scenario === key ? 'pill-rote' : ''}`}
          >
            {label}
          </button>
        ))}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b hairline text-xs text-[var(--color-ink-faint)]">
            <th className="py-2 text-left font-medium">signal</th>
            <th className="py-2 text-left font-medium">value</th>
            <th className="py-2 text-left font-medium">vs baseline</th>
            <th className="py-2 text-left font-medium">used by</th>
          </tr>
        </thead>
        <tbody className="font-mono text-xs">
          <tr className="border-b hairline-soft">
            <td className="py-2">size</td>
            <td className="py-2">{current.size} B</td>
            <td className="py-2">{current.size === baseline.size ? 'unchanged' : 'changed'}</td>
            <td className="py-2 text-[var(--color-ink-soft)]">paper + rote</td>
          </tr>
          <tr className="border-b hairline-soft">
            <td className="py-2">mtime_ns</td>
            <td className="py-2">{String(current.mtimeNs).slice(0, 19)}</td>
            <td className="py-2">
              {current.mtimeNs === baseline.mtimeNs ? 'unchanged' : 'changed'}
            </td>
            <td className="py-2 text-[var(--color-ink-soft)]">paper + rote</td>
          </tr>
          <tr className="border-b hairline-soft">
            <td className="py-2 text-[var(--color-rote)]">ctime_ns</td>
            <td className="py-2 text-[var(--color-rote)]">{String(current.ctimeNs).slice(0, 19)}</td>
            <td className="py-2 text-[var(--color-rote)]">
              {current.ctimeNs === baseline.ctimeNs ? 'unchanged' : 'changed'}
            </td>
            <td className="py-2 text-[var(--color-rote)]">rote only</td>
          </tr>
          <tr>
            <td className="py-2 text-[var(--color-rote)]">content_hash</td>
            <td className="py-2 text-[var(--color-rote)]">{current.contentHash}</td>
            <td className="py-2 text-[var(--color-rote)]">
              {current.contentHash === baseline.contentHash ? 'unchanged' : 'changed'}
            </td>
            <td className="py-2 text-[var(--color-rote)]">rote only</td>
          </tr>
        </tbody>
      </table>
      <p className="mt-4 text-sm">
        <span
          className={`pill ${current.cacheHits ? 'pill-rote' : ''}`}
          style={current.cacheHits ? {} : { borderColor: 'var(--color-warn)', color: 'var(--color-warn)' }}
        >
          {current.cacheHits ? 'cache hits' : 'cache misses'}
        </span>
        <span className="ml-3 text-[var(--color-ink-soft)]">
          {scenario === 'rewindMtime'
            ? 'Paper’s (size, mtime) would think the file is unchanged. ctime_ns moves anyway — userspace can’t backdate it without root.'
            : scenario === 'touchPreserveMtime'
              ? 'mtime was preserved manually. ctime_ns still moved on the inode write; content_hash settles it.'
              : scenario === 'normal'
                ? 'Every signal changes — both schemes agree.'
                : 'Nothing has changed yet.'}
        </span>
      </p>
    </div>
  );
}
