def timed(f):
    def wrapper(*a, **kw):
        return f(*a, **kw)
    return wrapper

@timed
def slow_sum(n):
    return sum(range(n))

def main():
    print(slow_sum(100))
    print(slow_sum(1000))

if __name__ == "__main__":
    main()
