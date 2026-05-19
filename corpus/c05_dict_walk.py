def freq(words):
    out = {}
    for w in words:
        out[w] = out.get(w, 0) + 1
    return out

def top(d, k):
    return sorted(d.items(), key=lambda kv: -kv[1])[:k]

def main():
    text = "to be or not to be that is the question"
    d = freq(text.split())
    for k, v in top(d, 3):
        print(k, v)

if __name__ == "__main__":
    main()
