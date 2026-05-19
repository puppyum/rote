from pathlib import PurePosixPath

def parts(p):
    return list(PurePosixPath(p).parts)

def main():
    for s in ["/a/b/c", "rel/path/file.txt", "/x"]:
        print(s, parts(s))

if __name__ == "__main__":
    main()
