/**
 * A small JS approximation of rote's canonical-source hashing.
 *
 * The real implementation lives in `src/rote/identity.py` and uses libcst
 * to parse a true AST, strip comments/docstrings/annotations/whitespace,
 * and rename bound variables to De Bruijn-style indices before blake3-
 * hashing the canonical string.
 *
 * This file is a lighter approximation that runs in the browser without
 * loading Pyodide. It demonstrates the *property* — the same observable
 * behaviour yields the same hash, while cosmetic edits do not change it.
 * The Python implementation is the source of truth; the editor widget
 * lets the viewer optionally swap in Pyodide for verification.
 *
 * Caveats explicitly:
 *  - Hash is SHA-256, not blake3 (web crypto has SHA-256 native).
 *  - The "rename" step is line-shaped, not true De Bruijn; it identifies
 *    parameter names from the def line and substitutes positional indices.
 *    Cross-function references aren't rewritten the way libcst would.
 *  - Type annotations are removed by regex, not by AST. Pathological
 *    nested generics could survive.
 *
 * For a faithful demo (the AST editor widget), this is good enough: it
 * gives instant feedback on the four kinds of edit the paper §3.2 / rote
 * §2.2 table calls out (comment / format / rename / literal).
 */

const enc = new TextEncoder();

/** Strip line comments and trailing whitespace. */
function stripComments(src: string): string {
  return src
    .split('\n')
    .map((line) => line.replace(/(^|[^"'\\])#.*$/, '$1').trimEnd())
    .join('\n');
}

/** Drop blank lines. */
function dropBlanks(src: string): string {
  return src
    .split('\n')
    .filter((l) => l.trim() !== '')
    .join('\n');
}

/** Drop triple-quoted docstrings that follow a def/class line. */
function stripDocstrings(src: string): string {
  return src.replace(
    /((?:^|\n)\s*(?:def|class)[^\n]*:\s*\n\s*)("""|''')([\s\S]*?)\2/g,
    '$1',
  );
}

/** Strip simple type annotations: `: type` in defs and `-> type:` returns. */
function stripAnnotations(src: string): string {
  // Return arrow.
  let out = src.replace(/\s*->\s*[^:\n]+(?=:)/g, '');
  // Parameter and variable annotations. We keep defaults: `x: int = 3` → `x = 3`.
  out = out.replace(/(\w+)\s*:\s*[^,)=\n]+(\s*[,)=\n])/g, '$1$2');
  return out;
}

/** Normalize horizontal whitespace inside non-leading positions. */
function normalizeSpacing(src: string): string {
  return src
    .split('\n')
    .map((line) => {
      const lead = line.match(/^\s*/)?.[0] ?? '';
      const rest = line.slice(lead.length).replace(/[ \t]+/g, ' ').trim();
      return lead.replace(/\t/g, '    ') + rest;
    })
    .join('\n');
}

/** Rename function parameters to positional indices `_p0, _p1, ...`.
 *
 * Implementation: walk each `def f(params):` header, parse parameter names,
 * rewrite the header to use positional indices, then globally substitute
 * each parameter name in the rest of the function body. We treat each
 * `def` block as the slice from the header through the next top-level
 * `def`/`class`/EOF — good enough for the demo's expectation that
 * `x` and `name` are the same identity.
 */
function renameParameters(src: string): string {
  const defRegex = /def\s+(\w+)\s*\(([^)]*)\)\s*:/g;
  // Collect all def matches first so we can determine block ranges.
  const matches: { fname: string; rawParams: string; start: number; end: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = defRegex.exec(src))) {
    matches.push({ fname: m[1], rawParams: m[2], start: m.index, end: m.index + m[0].length });
  }
  if (matches.length === 0) return src;

  // Build output by stitching segments and rewriting each block.
  const segments: string[] = [];
  let cursor = 0;
  for (let i = 0; i < matches.length; i++) {
    const cur = matches[i];
    const blockEnd = i + 1 < matches.length ? matches[i + 1].start : src.length;
    // Emit anything before the current def header verbatim.
    segments.push(src.slice(cursor, cur.start));
    // Parse parameter names (drop defaults).
    const names = cur.rawParams
      .split(',')
      .map((p) => p.trim().split('=')[0].trim())
      .filter(Boolean);
    // Rewrite header line.
    const newHeader = `def ${cur.fname}(${names.map((_n, idx) => `_p${idx}`).join(', ')}):`;
    // Rewrite body: replace each param name with its positional index globally
    // within this block.
    let body = src.slice(cur.end, blockEnd);
    names.forEach((name, idx) => {
      body = body.replace(new RegExp(`\\b${name}\\b`, 'g'), `_p${idx}`);
    });
    segments.push(newHeader, body);
    cursor = blockEnd;
  }
  // Emit anything after the last block.
  segments.push(src.slice(cursor));
  return segments.join('');
}

/** Produce the canonical source string used for hashing. */
export function canonicalSource(src: string): string {
  let s = src;
  s = stripComments(s);
  s = stripDocstrings(s);
  s = stripAnnotations(s);
  s = normalizeSpacing(s);
  s = renameParameters(s);
  s = dropBlanks(s);
  return s;
}

/** Hex-encoded SHA-256 of a string. */
export async function hash(s: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', enc.encode(s));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/** Convenience: canonicalize and hash in one call. */
export async function canonicalHash(src: string): Promise<{ canonical: string; digest: string }> {
  const canonical = canonicalSource(src);
  const digest = await hash(canonical);
  return { canonical, digest };
}
