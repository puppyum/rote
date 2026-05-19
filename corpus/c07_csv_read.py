import csv
import sys
from pathlib import Path

def gen(path, n=100):
    p = Path(path)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "v"])
        for i in range(n):
            w.writerow([i, i * 3])

def read_sum(path):
    p = Path(path)
    with p.open() as f:
        r = csv.reader(f)
        next(r)
        return sum(int(row[1]) for row in r)

def main():
    p = "c07.csv"
    gen(p, 50)
    print(read_sum(p))

if __name__ == "__main__":
    main()
