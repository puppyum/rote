def stats(xs):
    n = len(xs)
    s = sum(xs)
    return n, s, s / n

def main():
    n, s, m = stats([1, 2, 3, 4, 5])
    print(n, s, m)

if __name__ == "__main__":
    main()
