from typing import Any

import orjson

class JSONDecodeError(orjson.JSONDecodeError):
    pass

def dump_bytes(x) -> bytes:
    return orjson.dumps(x)

def dumps(x) -> str:
    return orjson.dumps(x).decode('utf-8')

def loads(s) -> Any:
    return orjson.loads(s)
