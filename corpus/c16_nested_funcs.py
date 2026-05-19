def double(x): return x * 2

def transform(xs, f):
    return [f(x) for x in xs]

def main():
    print(transform([1, 2, 3, 4, 5], double))
    print(transform([10, 20, 30], lambda x: x + 1))

if __name__ == "__main__":
    main()
