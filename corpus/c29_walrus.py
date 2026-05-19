def first_above(xs, threshold):
    if (m := max(xs, default=None)) is not None and m > threshold:
        return m
    return None

def main():
    print(first_above([1, 2, 3, 4, 5], 3))
    print(first_above([1, 2, 3], 10))

if __name__ == "__main__":
    main()
