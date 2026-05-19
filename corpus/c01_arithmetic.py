def add(a, b):
    return a + b

def mul(a, b):
    return a * b

def main():
    print(add(1, 2))
    print(mul(3, 4))
    print(add(mul(2, 3), mul(4, 5)))

if __name__ == "__main__":
    main()
