def squares(n):
    return [i * i for i in range(n)]

def evens(xs):
    return [x for x in xs if x % 2 == 0]

def main():
    s = squares(20)
    e = evens(s)
    print(sum(e))

if __name__ == "__main__":
    main()
