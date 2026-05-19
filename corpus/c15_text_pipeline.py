import re

def tokenize(t):
    return re.findall(r"\w+", t.lower())

def bigrams(toks):
    return list(zip(toks, toks[1:]))

def main():
    t = "The cat sat on the mat. The cat sat."
    toks = tokenize(t)
    bg = bigrams(toks)
    print(len(toks), len(bg))
    print(bg[:3])

if __name__ == "__main__":
    main()
