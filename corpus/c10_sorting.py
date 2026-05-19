def msort(xs):
    if len(xs) <= 1:
        return list(xs)
    mid = len(xs) // 2
    L = msort(xs[:mid])
    R = msort(xs[mid:])
    out = []
    i = j = 0
    while i < len(L) and j < len(R):
        if L[i] <= R[j]:
            out.append(L[i]); i += 1
        else:
            out.append(R[j]); j += 1
    out.extend(L[i:]); out.extend(R[j:])
    return out

def main():
    data = [5, 2, 9, 1, 7, 3, 8, 4, 6, 0]
    print(msort(data))

if __name__ == "__main__":
    main()
