def a(x):
    return b(x) + 1
def b(x):
    return c(x) * 2
def c(x):
    return d(x) - 3
def d(x):
    return x ** 2

def main():
    print(a(5))

if __name__ == "__main__":
    main()
