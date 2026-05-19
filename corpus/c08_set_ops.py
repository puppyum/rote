def union(a, b):
    return sorted(set(a) | set(b))

def intersect(a, b):
    return sorted(set(a) & set(b))

def main():
    a, b = [1,2,3,4], [3,4,5,6]
    print(union(a, b))
    print(intersect(a, b))

if __name__ == "__main__":
    main()
