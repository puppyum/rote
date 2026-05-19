"""Example: explicit @cache decorator usage — escape hatch for known-good functions."""

from __future__ import annotations

import time

import rote


@rote.cache
def slow_double(x: int) -> int:
    time.sleep(0.01)  # simulate work
    return x * 2


def main() -> None:
    t0 = time.perf_counter()
    for i in range(20):
        slow_double(i)
    cold = time.perf_counter() - t0

    t0 = time.perf_counter()
    for i in range(20):
        slow_double(i)
    warm = time.perf_counter() - t0

    print(f"cold: {cold*1000:.1f} ms")
    print(f"warm: {warm*1000:.1f} ms")
    print(rote.stats())


if __name__ == "__main__":
    main()
