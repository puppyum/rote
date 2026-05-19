from dataclasses import dataclass

@dataclass(frozen=True)
class Point:
    x: float
    y: float

def dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

def main():
    p, q = Point(0, 0), Point(3, 4)
    print(dist(p, q))

if __name__ == "__main__":
    main()
