def describe(p):
    match p:
        case (0, 0):
            return "origin"
        case (0, _):
            return "y-axis"
        case (_, 0):
            return "x-axis"
        case _:
            return "general"

def main():
    for p in [(0, 0), (0, 5), (3, 0), (1, 1)]:
        print(describe(p))

if __name__ == "__main__":
    main()
