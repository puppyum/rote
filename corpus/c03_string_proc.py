def words(text):
    return [w for w in text.split() if w]

def lengths(ws):
    return [len(w) for w in ws]

def total(ns):
    return sum(ns)

def main():
    t = "the quick brown fox jumps over the lazy dog"
    print(words(t))
    print(lengths(words(t)))
    print(total(lengths(words(t))))

if __name__ == "__main__":
    main()
