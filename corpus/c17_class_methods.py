class Counter:
    def __init__(self, start=0):
        self.n = start

    def inc(self, k=1):
        self.n += k
        return self.n

    def value(self):
        return self.n

def main():
    c = Counter(10)
    c.inc()
    c.inc(5)
    print(c.value())

if __name__ == "__main__":
    main()
