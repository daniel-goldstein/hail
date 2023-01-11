from typing import Any

import orjson

class JSONDecodeError(orjson.JSONDecodeError):
    pass

def dump_bytes(x) -> bytes:
    return orjson.dumps(x)

def dumps(x) -> str:
    return orjson.dumps(x).decode('utf-8')

def dump(x, out):
    out.write(orjson.dumps(x))

def loads(s) -> Any:
    return orjson.loads(s)

def load(f) -> Any:
    return loads(f.read())
