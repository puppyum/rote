def naturals(n):
    for i in range(n):
        yield i * i

def consume(g):
    return list(g)

def main():
    print(consume(naturals(10)))

if __name__ == "__main__":
    main()
