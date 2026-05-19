def fact(n):
    return 1 if n <= 1 else n * fact(n - 1)

def main():
    for n in [0, 1, 5, 10]:
        print(n, fact(n))

if __name__ == "__main__":
    main()
