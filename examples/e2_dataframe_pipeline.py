"""Example: a multi-stage DataFrame pipeline (read → transform → aggregate)."""

from __future__ import annotations

import csv
from pathlib import Path


def write_input(path: Path, rows: int = 10_000) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "v"])
        for i in range(rows):
            w.writerow([i, i * 2 + (i % 7)])


def load(path: Path) -> list[tuple[int, int]]:
    with path.open() as f:
        r = csv.reader(f)
        next(r)  # skip header
        return [(int(a), int(b)) for a, b in r]


def transform(rows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return [(i, v * v) for i, v in rows if v % 2 == 0]


def aggregate(rows: list[tuple[int, int]]) -> dict[str, float]:
    if not rows:
        return {"n": 0, "mean": 0.0, "max": 0.0}
    vs = [v for _, v in rows]
    return {"n": len(vs), "mean": sum(vs) / len(vs), "max": float(max(vs))}


def main() -> None:
    p = Path("e2_input.csv")
    write_input(p, rows=200_000)
    data = load(p)
    even = transform(data)
    agg = aggregate(even)
    print(agg)


if __name__ == "__main__":
    main()
