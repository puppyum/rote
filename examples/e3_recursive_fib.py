"""Example: recursion exercise — pure function, exponential without memo."""

from __future__ import annotations

import sys


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 28
    print(fib(n))


if __name__ == "__main__":
    main()
