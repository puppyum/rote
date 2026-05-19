def greet(name, greeting="hello", punct="!"):
    return f"{greeting}, {name}{punct}"

def main():
    print(greet("world"))
    print(greet("there", greeting="hi"))
    print(greet("you", punct="."))

if __name__ == "__main__":
    main()
