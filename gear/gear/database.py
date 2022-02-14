import asyncio
import contextlib
import functools
import logging
import os
import ssl
import traceback
from concurrent.futures import Executor
from typing import Any, List, Optional

import MySQLdb
import MySQLdb.connections
import MySQLdb.cursors

from gear.metrics import PrometheusSQLTimer
from hailtop.auth.sql_config import SQLConfig
from hailtop.utils import blocking_to_async, sleep_and_backoff

log = logging.getLogger('gear.database')


# 1040 - Too many connections
# 1213 - Deadlock found when trying to get lock; try restarting transaction
# 2003 - Can't connect to MySQL server on ...
# 2013 - Lost connection to MySQL server during query ([Errno 104] Connection reset by peer)
operational_error_retry_codes = (1040, 1213, 2003, 2013)
# 1205 - Lock wait timeout exceeded; try restarting transaction
internal_error_retry_codes = (1205,)


def retry_transient_mysql_errors(f):
    @functools.wraps(f)
    async def wrapper(*args, **kwargs):
        delay = 0.1
        while True:
            try:
                return await f(*args, **kwargs)
            except MySQLdb.InternalError as e:
                if e.args[0] in internal_error_retry_codes:
                    log.warning(
                        f'encountered pymysql error, retrying {e}',
                        exc_info=True,
                        extra={'full_stacktrace': '\n'.join(traceback.format_stack())},
                    )
                else:
                    raise
            except MySQLdb.OperationalError as e:
                if e.args[0] in operational_error_retry_codes:
                    log.warning(
                        f'encountered mysql error, retrying {e}',
                        exc_info=True,
                        extra={'full_stacktrace': '\n'.join(traceback.format_stack())},
                    )
                else:
                    raise
            delay = await sleep_and_backoff(delay)

    return wrapper


def transaction(db, **transaction_kwargs):
    def transformer(fun):
        @functools.wraps(fun)
        @retry_transient_mysql_errors
        async def wrapper(*args, **kwargs):
            async with db.start(**transaction_kwargs) as tx:
                return await fun(tx, *args, **kwargs)

        return wrapper

    return transformer


async def aenter(acontext_manager):
    return await acontext_manager.__aenter__()


async def aexit(acontext_manager, exc_type=None, exc_val=None, exc_tb=None):
    return await acontext_manager.__aexit__(exc_type, exc_val, exc_tb)


def get_sql_config(maybe_config_file: Optional[str] = None) -> SQLConfig:
    if maybe_config_file is None:
        config_file = os.environ.get('HAIL_DATABASE_CONFIG_FILE', '/sql-config/sql-config.json')
    else:
        config_file = maybe_config_file
    with open(config_file, 'r') as f:
        sql_config = SQLConfig.from_json(f.read())
    sql_config.check()
    log.info('using tls and verifying server certificates for MySQL')
    return sql_config


database_ssl_context = None


def get_database_ssl_context(sql_config: Optional[SQLConfig] = None) -> ssl.SSLContext:
    global database_ssl_context
    if database_ssl_context is None:
        if sql_config is None:
            sql_config = get_sql_config()
        database_ssl_context = ssl.create_default_context(cafile=sql_config.ssl_ca)
        database_ssl_context.load_cert_chain(sql_config.ssl_cert, keyfile=sql_config.ssl_key, password=None)
        database_ssl_context.verify_mode = ssl.CERT_REQUIRED
        database_ssl_context.check_hostname = False
    return database_ssl_context


class MySQLAsyncCursor:
    def __init__(self, executor: Executor, cursor: MySQLdb.cursors.Cursor):
        self.executor = executor
        self.cursor = cursor

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    async def execute(self, sql: str, args=None):
        if args is not None and not (isinstance(args, tuple) or isinstance(args, list)):
            args = tuple([args])
        return await blocking_to_async(self.executor, self.cursor.execute, sql, args)

    async def executemany(self, sql: List[str], args: List[Any]):
        return await blocking_to_async(self.executor, self.cursor.executemany, sql, args)

    async def fetchone(self):
        return await blocking_to_async(self.executor, self.cursor.fetchone)

    async def fetchmany(self, count: int):
        return await blocking_to_async(self.executor, self.cursor.fetchmany, count)

    async def __aenter__(self):
        return self

    # TODO Add safety measures to close connection if necessary
    # and ensure cursor and parent connection are only in use by one
    # thread at a time
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await blocking_to_async(self.executor, self.cursor.close)


class MySQLAsyncConnection:
    def __init__(self, executor: Executor, conn: MySQLdb.connections.Connection):
        self.executor = executor
        self.conn = conn

    def cursor(self) -> MySQLAsyncCursor:
        return MySQLAsyncCursor(self.executor, self.conn.cursor())

    async def commit(self):
        await blocking_to_async(self.executor, self.conn.commit)

    async def rollback(self):
        await blocking_to_async(self.executor, self.conn.rollback)

    def close(self):
        self.conn.close()


class MySQLConnectionPool:
    def __init__(self, executor: Executor, config_file: Optional[str], autocommit: bool, maxsize: int):
        self.executor = executor
        self.sql_config = get_sql_config(config_file)
        self.ssl_context = {
            'ca': self.sql_config.ssl_ca,
            'cert': self.sql_config.ssl_cert,
            'key': self.sql_config.ssl_key,
        }
        self.autocommit = autocommit
        self.maxsize = maxsize
        self.connections: List[MySQLAsyncConnection] = []
        self.free_connections = asyncio.Queue(maxsize=maxsize)

    @contextlib.asynccontextmanager
    async def acquire(self):
        if self.free_connections.empty() and len(self.connections) < self.maxsize:
            conn = self._new_connection()
            self.connections.append(conn)
        else:
            conn = await self.free_connections.get()

        try:
            yield conn
        finally:
            self.free_connections.put_nowait(conn)

    def close(self):
        for conn in self.connections:
            conn.close()

    def _new_connection(self) -> MySQLAsyncConnection:
        return MySQLAsyncConnection(
            self.executor,
            MySQLdb.connect(
                host=self.sql_config.host,
                user=self.sql_config.user,
                password=self.sql_config.password,
                database=self.sql_config.db,
                port=self.sql_config.port,
                charset='utf8',
                ssl=self.ssl_context,
                cursorclass=MySQLdb.cursors.DictCursor,
                autocommit=self.autocommit,
            ),
        )


@retry_transient_mysql_errors
async def create_database_pool(
    executor: Executor, config_file: str = None, autocommit: bool = True, maxsize: int = 10
) -> MySQLConnectionPool:
    return MySQLConnectionPool(executor, config_file, autocommit, maxsize)


async def _release_connection(conn_context_manager):
    if conn_context_manager is not None:
        try:
            await aexit(conn_context_manager)
        except:
            log.exception('while releasing database connection')


class Transaction:
    def __init__(self):
        self.conn_context_manager = None
        self.conn: Optional[MySQLAsyncConnection] = None

    async def async_init(self, db_pool: MySQLConnectionPool, read_only: bool):
        try:
            self.conn_context_manager = db_pool.acquire()
            self.conn = await aenter(self.conn_context_manager)
            async with self.conn.cursor() as cursor:
                if read_only:
                    await cursor.execute('START TRANSACTION READ ONLY;')
                else:
                    await cursor.execute('START TRANSACTION;')
        except:
            self.conn = None
            conn_context_manager = self.conn_context_manager
            self.conn_context_manager = None
            asyncio.ensure_future(_release_connection(conn_context_manager))
            raise

    async def _aexit_1(self, exc_type):
        try:
            if self.conn is not None:
                if exc_type:
                    await self.conn.rollback()
                else:
                    await self.conn.commit()
        except:
            log.info('while exiting transaction', exc_info=True)
            raise
        finally:
            self.conn = None
            conn_context_manager = self.conn_context_manager
            self.conn_context_manager = None
            asyncio.ensure_future(_release_connection(conn_context_manager))

    async def _aexit(self, exc_type, exc_val, exc_tb):  # pylint: disable=unused-argument
        # cancelling cleanup could leak a connection
        # await shield becuase we want to wait for commit/rollback to finish
        await asyncio.shield(self._aexit_1(exc_type))

    async def just_execute(self, sql, args=None):
        assert self.conn
        async with self.conn.cursor() as cursor:
            await cursor.execute(sql, args)

    async def execute_and_fetchone(self, sql, args=None, query_name=None):
        assert self.conn
        async with self.conn.cursor() as cursor:
            if query_name is None:
                await cursor.execute(sql, args)
            else:
                async with PrometheusSQLTimer(query_name):
                    await cursor.execute(sql, args)
            return await cursor.fetchone()

    async def execute_and_fetchall(self, sql, args=None, query_name=None):
        assert self.conn
        async with self.conn.cursor() as cursor:
            if query_name is None:
                await cursor.execute(sql, args)
            else:
                async with PrometheusSQLTimer(query_name):
                    await cursor.execute(sql, args)
            while True:
                rows = await cursor.fetchmany(100)
                if not rows:
                    break
                for row in rows:
                    yield row

    async def execute_insertone(self, sql, args=None):
        assert self.conn
        async with self.conn.cursor() as cursor:
            await cursor.execute(sql, args)
            return cursor.lastrowid

    async def execute_update(self, sql, args=None):
        assert self.conn
        async with self.conn.cursor() as cursor:
            return await cursor.execute(sql, args)

    async def execute_many(self, sql, args_array):
        assert self.conn
        async with self.conn.cursor() as cursor:
            return await cursor.executemany(sql, args_array)


class TransactionAsyncContextManager:
    def __init__(self, db_pool: MySQLConnectionPool, read_only: bool):
        self.db_pool: MySQLConnectionPool = db_pool
        self.read_only: bool = read_only
        self.tx: Optional['Transaction'] = None

    async def __aenter__(self):
        tx = Transaction()
        await tx.async_init(self.db_pool, self.read_only)
        self.tx = tx
        return tx

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.tx._aexit(exc_type, exc_val, exc_tb)
        self.tx = None


class CallError(Exception):
    def __init__(self, rv):
        super().__init__(rv)
        self.rv = rv


class Database:
    def __init__(self):
        self.pool: Optional[MySQLConnectionPool] = None

    async def async_init(self, pool: Executor, config_file=None, maxsize=10):
        self.pool = await create_database_pool(pool, config_file=config_file, autocommit=False, maxsize=maxsize)

    def start(self, read_only: bool = False):
        assert self.pool
        return TransactionAsyncContextManager(self.pool, read_only)

    @retry_transient_mysql_errors
    async def just_execute(self, sql, args=None):
        async with self.start() as tx:
            await tx.just_execute(sql, args)

    @retry_transient_mysql_errors
    async def execute_and_fetchone(self, sql, args=None, query_name=None):
        async with self.start() as tx:
            return await tx.execute_and_fetchone(sql, args, query_name)

    @retry_transient_mysql_errors
    async def select_and_fetchone(self, sql, args=None, query_name=None):
        async with self.start(read_only=True) as tx:
            return await tx.execute_and_fetchone(sql, args, query_name)

    async def execute_and_fetchall(self, sql, args=None, query_name=None):
        async with self.start() as tx:
            async for row in tx.execute_and_fetchall(sql, args, query_name):
                yield row

    async def select_and_fetchall(self, sql, args=None, query_name=None):
        async with self.start(read_only=True) as tx:
            async for row in tx.execute_and_fetchall(sql, args, query_name):
                yield row

    @retry_transient_mysql_errors
    async def execute_insertone(self, sql, args=None):
        async with self.start() as tx:
            return await tx.execute_insertone(sql, args)

    @retry_transient_mysql_errors
    async def execute_update(self, sql, args=None):
        async with self.start() as tx:
            return await tx.execute_update(sql, args)

    @retry_transient_mysql_errors
    async def execute_many(self, sql, args_array):
        async with self.start() as tx:
            return await tx.execute_many(sql, args_array)

    @retry_transient_mysql_errors
    async def check_call_procedure(self, sql, args=None, query_name=None):
        rv = await self.execute_and_fetchone(sql, args, query_name)
        if rv['rc'] != 0:
            raise CallError(rv)
        return rv

    # async for backwards-compatibility with migration code
    async def async_close(self):
        self.pool.close()
