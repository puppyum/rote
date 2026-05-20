import { describe, expect, it } from 'vitest';
import { canonicalSource, hash } from './canonical';

/**
 * Property tests for the canonical-AST hash approximation used by the
 * AST editor widget. Mirrors the Python `tests/property/test_identity_properties.py`
 * harness: invariance under cosmetic edits, sensitivity to semantic edits.
 *
 * The JS implementation is an approximation — same property, weaker engine.
 */

const baseline = `def f(x: int) -> int:
    """docstring."""
    # comment
    y = x + 1
    return y
`;

describe('canonicalSource — invariance (paper §3.2 / rote §2.2)', () => {
  it('strips line comments', () => {
    const noComments = `def f(x: int) -> int:
    """docstring."""
    y = x + 1
    return y
`;
    expect(canonicalSource(baseline)).toBe(canonicalSource(noComments));
  });

  it('strips docstrings', () => {
    const noDoc = `def f(x: int) -> int:
    # comment
    y = x + 1
    return y
`;
    expect(canonicalSource(baseline)).toBe(canonicalSource(noDoc));
  });

  it('strips parameter and return annotations', () => {
    const noAnn = `def f(x):
    """docstring."""
    # comment
    y = x + 1
    return y
`;
    expect(canonicalSource(baseline)).toBe(canonicalSource(noAnn));
  });

  it('renames bound parameters consistently', () => {
    const renamed = `def f(name: int) -> int:
    """docstring."""
    # comment
    y = name + 1
    return y
`;
    expect(canonicalSource(baseline)).toBe(canonicalSource(renamed));
  });
});

describe('canonicalSource — sensitivity (semantic edits change the hash)', () => {
  it('changes when a literal changes', () => {
    const changed = baseline.replace('+ 1', '+ 2');
    expect(canonicalSource(baseline)).not.toBe(canonicalSource(changed));
  });

  it('changes when an operator changes', () => {
    const changed = baseline.replace('+ 1', '- 1');
    expect(canonicalSource(baseline)).not.toBe(canonicalSource(changed));
  });
});

describe('hash', () => {
  it('is deterministic for the same input', async () => {
    const a = await hash('hello');
    const b = await hash('hello');
    expect(a).toBe(b);
  });

  it('produces a 64-character hex SHA-256', async () => {
    const h = await hash('rote');
    expect(h).toMatch(/^[0-9a-f]{64}$/);
  });
});
