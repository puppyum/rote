"""Example: a deterministic slow computation that benefits from memoization."""

from __future__ import annotations

import math


def slow_pi(n_terms: int) -> float:
    # Leibniz series — slow on purpose so the wrapper has something to cache.
    total = 0.0
    for k in range(n_terms):
        total += ((-1) ** k) / (2 * k + 1)
    return 4.0 * total


def main() -> None:
    n = 5_000_000
    approx = slow_pi(n)
    err = abs(approx - math.pi)
    print(f"pi ≈ {approx:.10f}  (err {err:.2e})")


if __name__ == "__main__":
    main()
