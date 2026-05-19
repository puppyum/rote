def fb(n):
    if n % 15 == 0: return "fizzbuzz"
    if n % 3 == 0: return "fizz"
    if n % 5 == 0: return "buzz"
    return str(n)

def main():
    for n in range(1, 16):
        print(fb(n))

if __name__ == "__main__":
    main()
