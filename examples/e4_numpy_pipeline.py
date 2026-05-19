"""Example: numpy linear algebra — typical research workflow."""

from __future__ import annotations

import numpy as np


def synth(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(n, n))


def factor(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    q, r = np.linalg.qr(a)
    return q, r


def stats(a: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(a.mean()),
        "std": float(a.std()),
        "max": float(a.max()),
        "min": float(a.min()),
    }


def main() -> None:
    a = synth(600, seed=42)
    q, r = factor(a)
    s = stats(r)
    print(s)


if __name__ == "__main__":
    main()
