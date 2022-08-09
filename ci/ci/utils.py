import asyncio
import secrets
import string
import json
import os
import random

from typing import Set

from gear import Database
from hailtop.utils.time import time_msecs

namespace_reservation_lock = asyncio.Lock()


def generate_token(size=12):
    assert size > 0
    alpha = string.ascii_lowercase
    alnum = string.ascii_lowercase + string.digits
    return secrets.choice(alpha) + ''.join([secrets.choice(alnum) for _ in range(size - 1)])


async def allocate_namespace(db: Database) -> str:
    internal_namespaces: Set[str] = set(json.loads(os.environ.get('HAIL_INTERNAL_NAMESPACES', '[]')))

    async with namespace_reservation_lock:
        reserved_namespaces = {
            record['namespace_name']
            async for record in db.select_and_fetchall('SELECT namespace_name from internal_namespaces;')
        }
        available_namespaces = internal_namespaces - reserved_namespaces
        if len(available_namespaces) == 0:
            raise ValueError('No available namespaces')
        ns = random.choice(list(available_namespaces))
        await db.execute_insertone(
            '''INSERT INTO internal_namespaces (`namespace_name`, `expiration_time`) VALUES (%s, %s)''',
            (ns, time_msecs()),
        )
        return ns
