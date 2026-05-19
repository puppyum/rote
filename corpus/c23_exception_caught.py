def safe_div(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return float("inf")

def main():
    print(safe_div(10, 2))
    print(safe_div(7, 0))

if __name__ == "__main__":
    main()
