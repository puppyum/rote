import json

def parse(s):
    return json.loads(s)

def serialize(obj):
    return json.dumps(obj, sort_keys=True)

def main():
    s = '{"a": 1, "b": [2, 3]}'
    obj = parse(s)
    print(serialize(obj))

if __name__ == "__main__":
    main()
