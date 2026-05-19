def make_adder(n):
    def add(x):
        return x + n
    return add

def main():
    add5 = make_adder(5)
    print(add5(10), add5(20))

if __name__ == "__main__":
    main()
