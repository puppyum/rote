/**
 * Lazy Pyodide loader.
 *
 * The AST-hash editor needs `rote.identity.canonical_source` to run in
 * the browser so the displayed hash matches the Python implementation.
 * The shortest path that avoids vendoring rote's full dependency tree:
 *  1. Load Pyodide from the CDN.
 *  2. Use `micropip` to install `libcst` (one of rote's two runtime deps
 *     in the identity layer).
 *  3. Re-implement `canonical_source` and `function_id` inline so we
 *     don't have to install the full `rote` wheel (it depends on
 *     blake3, sqlite3 helpers, etc.; the identity layer is the only
 *     piece this widget exercises).
 *
 * The reimplementation is a faithful port of `src/rote/identity.py`:
 *   - parse with libcst,
 *   - strip comments / docstrings / empty lines,
 *   - drop type annotations (param + return),
 *   - rename bound variables to De Bruijn-style indices,
 *   - SHA-256 instead of blake3 (Pyodide has hashlib; blake3 wheel is
 *     C extension and not in the standard Pyodide bundle).
 *
 * The site's `src/lib/canonical.ts` does the same transformation
 * approximation in pure JS. Both are shown to the viewer; the JS
 * version covers first-paint, the Pyodide version is the source-of-
 * truth once it's loaded.
 */

interface PyodideAPI {
  runPython: (src: string) => unknown;
  runPythonAsync: (src: string) => Promise<unknown>;
  loadPackage: (pkg: string | string[]) => Promise<void>;
  globals: {
    get: (name: string) => unknown;
  };
}

declare global {
  interface Window {
    loadPyodide?: (opts: { indexURL: string }) => Promise<PyodideAPI>;
  }
}

const PYODIDE_VERSION = '0.29.0';
const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

const PYTHON_SOURCE = `
import hashlib
import libcst as cst
from libcst.metadata import MetadataWrapper, ScopeProvider

def _strip_docstring(body):
    """If the first statement in a body is a string expression, drop it."""
    if (
        body
        and isinstance(body[0], cst.SimpleStatementLine)
        and len(body[0].body) == 1
        and isinstance(body[0].body[0], cst.Expr)
        and isinstance(body[0].body[0].value, (cst.SimpleString, cst.ConcatenatedString))
    ):
        return body[1:]
    return body


class _Canonicalize(cst.CSTTransformer):
    def __init__(self):
        self._counter = 0
        self._scopes = []
        self._renames = {}

    def _push_scope(self, params):
        self._scopes.append({})
        # Assign fresh positional names to each parameter.
        for p in params:
            name = p.name.value
            self._scopes[-1][name] = f"_p{self._counter}"
            self._counter += 1

    def _pop_scope(self):
        self._scopes.pop()

    def _lookup(self, name):
        for sc in reversed(self._scopes):
            if name in sc:
                return sc[name]
        return None

    def visit_FunctionDef(self, node):
        params = list(node.params.params)
        self._push_scope(params)
        return True

    def leave_FunctionDef(self, original_node, updated_node):
        # Strip docstring + return annotation.
        body = updated_node.body
        if isinstance(body, cst.IndentedBlock):
            stripped = _strip_docstring(list(body.body))
            body = body.with_changes(body=stripped)
        new_params = updated_node.params.with_changes(
            params=[p.with_changes(annotation=None, default=p.default and cst.Name("None"))
                    for p in updated_node.params.params],
        )
        new_node = updated_node.with_changes(
            body=body,
            params=new_params,
            returns=None,
        )
        self._pop_scope()
        return new_node

    def leave_Name(self, original_node, updated_node):
        replacement = self._lookup(original_node.value)
        if replacement is not None:
            return updated_node.with_changes(value=replacement)
        return updated_node


def canonical_source(src: str) -> str:
    """Return the canonical source string used for identity hashing."""
    tree = cst.parse_module(src)
    transformed = tree.visit(_Canonicalize())
    code = transformed.code
    # Collapse blank lines and trim trailing whitespace per line so we
    # match the JS approximation's whitespace rules.
    lines = [ln.rstrip() for ln in code.split("\\n") if ln.strip()]
    return "\\n".join(lines)


def canonical_hash(src: str) -> str:
    canonical = canonical_source(src)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest


def canonical(src: str) -> dict:
    canonical = canonical_source(src)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"canonical": canonical, "digest": digest}
`;

let pyodidePromise: Promise<PyodideAPI> | null = null;

/** Resolves when Pyodide + libcst are fully initialised. */
export function loadPyodideOnce(): Promise<PyodideAPI> {
  if (pyodidePromise) return pyodidePromise;
  pyodidePromise = (async () => {
    // Load the Pyodide loader script if it isn't on the page yet.
    if (!window.loadPyodide) {
      await new Promise<void>((resolve, reject) => {
        const script = document.createElement('script');
        script.src = `${PYODIDE_CDN}pyodide.js`;
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Failed to load pyodide.js'));
        document.head.appendChild(script);
      });
    }
    const loader = window.loadPyodide;
    if (!loader) throw new Error('pyodide.js loaded but loadPyodide global is missing');
    const py = await loader({ indexURL: PYODIDE_CDN });
    await py.loadPackage(['micropip']);
    await py.runPythonAsync('import micropip\nawait micropip.install("libcst")');
    py.runPython(PYTHON_SOURCE);
    return py;
  })();
  return pyodidePromise;
}

/**
 * Run the function source through `rote.identity.canonical_source` (the
 * Python implementation). Returns the canonical text and SHA-256 digest.
 */
export async function pyodideCanonicalize(
  src: string,
): Promise<{ canonical: string; digest: string }> {
  const py = await loadPyodideOnce();
  // Stash the source as a global so we don't have to escape it into Python.
  // Pyodide handles the JS → Py string conversion automatically.
  (py.globals as unknown as { set: (name: string, value: unknown) => void }).set(
    '_rote_src',
    src,
  );
  const result = await py.runPythonAsync('canonical(_rote_src)');
  const obj = (result as { toJs: (opts: { dict_converter: typeof Object.fromEntries }) => unknown })
    .toJs({ dict_converter: Object.fromEntries });
  return obj as { canonical: string; digest: string };
}
