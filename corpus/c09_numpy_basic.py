import numpy as np

def matmul(a, b):
    return a @ b

def trace(m):
    return float(np.trace(m))

def main():
    a = np.arange(12).reshape(3, 4).astype(float)
    b = np.arange(12).reshape(4, 3).astype(float)
    c = matmul(a, b)
    print(round(trace(c), 4))

if __name__ == "__main__":
    main()
