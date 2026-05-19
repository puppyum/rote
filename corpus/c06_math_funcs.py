import math

def hypot(a, b):
    return math.sqrt(a * a + b * b)

def angle(a, b):
    return math.atan2(b, a)

def main():
    for a, b in [(3, 4), (5, 12), (8, 15)]:
        print(hypot(a, b), round(angle(a, b), 5))

if __name__ == "__main__":
    main()
