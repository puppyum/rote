def group(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out

def main():
    rows = [
        {"city": "nyc", "x": 1},
        {"city": "sea", "x": 2},
        {"city": "nyc", "x": 3},
        {"city": "sea", "x": 4},
    ]
    g = group(rows, "city")
    for k in sorted(g):
        print(k, sum(r["x"] for r in g[k]))

if __name__ == "__main__":
    main()
