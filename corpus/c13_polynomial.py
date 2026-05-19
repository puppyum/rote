def horner(coeffs, x):
    acc = 0.0
    for c in coeffs:
        acc = acc * x + c
    return acc

def main():
    coeffs = [1, -3, 2, 5]
    for x in [-2.0, -1.0, 0.0, 1.0, 2.0]:
        print(round(horner(coeffs, x), 6))

if __name__ == "__main__":
    main()
