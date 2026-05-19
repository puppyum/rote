from itertools import chain, islice

def first_n(it, n):
    return list(islice(it, n))

def main():
    a = range(5)
    b = range(10, 15)
    chained = chain(a, b)
    print(first_n(chained, 7))

if __name__ == "__main__":
    main()
