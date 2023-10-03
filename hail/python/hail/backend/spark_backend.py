from typing import Set, Tuple
import sys
import os
import json
import socket
import socketserver
from threading import Thread
import py4j
import pyspark
import pyspark.sql

import orjson
from typing import List, Optional

import hail as hl
from hail.utils.java import scala_package_object
from hail.expr.table_type import ttable
from hail.fs.hadoop_fs import HadoopFS
from hail.ir.renderer import CSERenderer
from hail.table import Table
from hail.matrixtable import MatrixTable
from hailtop.aiotools.router_fs import RouterAsyncFS
from hailtop.aiotools.validators import validate_file

from .py4j_backend import Py4JBackend, handle_java_exception, action_routes
from ..hail_logging import Logger
from .backend import local_jar_information, fatal_error_from_java_error_triplet


_installed = False
_original = None


def install_exception_handler():
    global _installed
    global _original
    if not _installed:
        _original = py4j.protocol.get_return_value
        _installed = True
        # The original `get_return_value` is not patched, it's idempotent.
        patched = handle_java_exception(_original)
        # only patch the one used in py4j.java_gateway (call Java API)
        py4j.java_gateway.get_return_value = patched


def uninstall_exception_handler():
    global _installed
    global _original
    if _installed:
        _installed = False
        py4j.protocol.get_return_value = _original


class LoggingTCPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            sys.stderr.write(line.decode("ISO-8859-1"))


class SimpleServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class):
        socketserver.TCPServer.__init__(self, server_address, handler_class)


def connect_logger(utils_package_object, host, port):
    """
    This method starts a simple server which listens on a port for a
    client to connect and start writing messages. Whenever a message
    is received, it is written to sys.stderr. The server is run in
    a daemon thread from the caller, which is killed when the caller
    thread dies.

    If the socket is in use, then the server tries to listen on the
    next port (port + 1). After 25 tries, it gives up.

    :param str host: Hostname for server.
    :param int port: Port to listen on.
    """
    server = None
    tries = 0
    max_tries = 25
    while not server:
        try:
            server = SimpleServer((host, port), LoggingTCPHandler)
        except socket.error:
            port += 1
            tries += 1

            if tries >= max_tries:
                sys.stderr.write(
                    'WARNING: Could not find a free port for logger, maximum retries {} exceeded.'.format(max_tries))
                return

    t = Thread(target=server.serve_forever, args=())

    # The thread should be a daemon so that it shuts down when the parent thread is killed
    t.daemon = True

    t.start()
    utils_package_object.addSocketAppender(host, port)


class Log4jLogger(Logger):
    def __init__(self, log_pkg):
        self._log_pkg = log_pkg

    def error(self, msg):
        self._log_pkg.error(msg)

    def warning(self, msg):
        self._log_pkg.warn(msg)

    def info(self, msg):
        self._log_pkg.info(msg)


def append_to_comma_separated_list(conf: pyspark.SparkConf, k: str, *new_values: str):
    old = conf.get(k, None)
    if old is None:
        conf.set(k, ','.join(new_values))
    else:
        conf.set(k, old + ',' + ','.join(new_values))


class SparkBackend(Py4JBackend):
    def __init__(self, idempotent, sc, spark_conf, app_name, master,
                 local, log, quiet, append, min_block_size,
                 branching_factor, tmpdir, local_tmpdir, skip_logging_configuration, optimizer_iterations,
                 *,
                 gcs_requester_pays_project: Optional[str] = None,
                 gcs_requester_pays_buckets: Optional[str] = None
                 ):
        super(SparkBackend, self).__init__()
        assert gcs_requester_pays_project is not None or gcs_requester_pays_buckets is None

        try:
            local_jar_info = local_jar_information()
        except ValueError:
            local_jar_info = None

        if local_jar_info is not None:
            conf = pyspark.SparkConf()

            base_conf = spark_conf or {}
            for k, v in base_conf.items():
                conf.set(k, v)

            jars = [local_jar_info.path]
            extra_classpath = local_jar_info.extra_classpath

            if os.environ.get('HAIL_SPARK_MONITOR') or os.environ.get('AZURE_SPARK') == '1':
                import sparkmonitor
                jars.append(os.path.join(os.path.dirname(sparkmonitor.__file__), 'listener.jar'))
                append_to_comma_separated_list(
                    conf,
                    'spark.extraListeners',
                    'sparkmonitor.listener.JupyterSparkMonitorListener'
                )

            append_to_comma_separated_list(
                conf,
                'spark.jars',
                *jars
            )
            if os.environ.get('AZURE_SPARK') == '1':
                print('AZURE_SPARK environment variable is set to "1", assuming you are in HDInsight.')
                # Setting extraClassPath in HDInsight overrides the classpath entirely so you can't
                # load the Scala standard library. Interestingly, setting extraClassPath is not
                # necessary in HDInsight.
            else:
                append_to_comma_separated_list(
                    conf,
                    'spark.driver.extraClassPath',
                    *jars,
                    *extra_classpath
                )
                append_to_comma_separated_list(
                    conf,
                    'spark.executor.extraClassPath',
                    './hail-all-spark.jar',
                    *extra_classpath
                )

            if sc is None:
                pyspark.SparkContext._ensure_initialized(conf=conf)
            elif not quiet:
                sys.stderr.write(
                    'pip-installed Hail requires additional configuration options in Spark referring\n'
                    '  to the path to the Hail Python module directory HAIL_DIR,\n'
                    '  e.g. /path/to/python/site-packages/hail:\n'
                    '    spark.jars=HAIL_DIR/backend/hail-all-spark.jar\n'
                    '    spark.driver.extraClassPath=HAIL_DIR/backend/hail-all-spark.jar\n'
                    '    spark.executor.extraClassPath=./hail-all-spark.jar')
        else:
            pyspark.SparkContext._ensure_initialized()

        self._gateway = pyspark.SparkContext._gateway
        self._jvm = pyspark.SparkContext._jvm

        hail_package = getattr(self._jvm, 'is').hail

        self._hail_package = hail_package
        self._utils_package_object = scala_package_object(hail_package.utils)

        jsc = sc._jsc.sc() if sc else None

        if idempotent:
            self._jbackend = hail_package.backend.spark.SparkBackend.getOrCreate(
                jsc, app_name, master, local, log, True, append, skip_logging_configuration, min_block_size, tmpdir, local_tmpdir,
                gcs_requester_pays_project, gcs_requester_pays_buckets)
            self._jhc = hail_package.HailContext.getOrCreate(
                self._jbackend, branching_factor, optimizer_iterations)
        else:
            self._jbackend = hail_package.backend.spark.SparkBackend.apply(
                jsc, app_name, master, local, log, True, append, skip_logging_configuration, min_block_size, tmpdir, local_tmpdir,
                gcs_requester_pays_project, gcs_requester_pays_buckets)
            self._jhc = hail_package.HailContext.apply(
                self._jbackend, branching_factor, optimizer_iterations)

        self._backend_server = hail_package.backend.BackendServer.apply(self._jbackend)
        self._backend_server_port: int = self._backend_server.port()
        self._backend_server.start()
        self._jsc = self._jbackend.sc()
        if sc:
            self.sc = sc
        else:
            self.sc = pyspark.SparkContext(gateway=self._gateway, jsc=self._jvm.JavaSparkContext(self._jsc))
        self._jspark_session = self._jbackend.sparkSession()
        self._spark_session = pyspark.sql.SparkSession(self.sc, self._jspark_session)
        self._registered_ir_function_names: Set[str] = set()

        # This has to go after creating the SparkSession. Unclear why.
        # Maybe it does its own patch?
        install_exception_handler()

        from hail.context import version

        py_version = version()
        jar_version = self._jhc.version()
        if jar_version != py_version:
            raise RuntimeError(f"Hail version mismatch between JAR and Python library\n"
                               f"  JAR:    {jar_version}\n"
                               f"  Python: {py_version}")

        self._fs = None
        self._logger = None

        if not quiet:
            sys.stderr.write('Running on Apache Spark version {}\n'.format(self.sc.version))
            if self._jsc.uiWebUrl().isDefined():
                sys.stderr.write('SparkUI available at {}\n'.format(self._jsc.uiWebUrl().get()))

            self._jbackend.startProgressBar()

        self._initialize_flags({})

        self._router_async_fs = RouterAsyncFS(
            gcs_kwargs={"gcs_requester_pays_configuration": gcs_requester_pays_project}
        )

    def validate_file(self, uri: str) -> None:
        validate_file(uri, self._router_async_fs)

    def jvm(self):
        return self._jvm

    def hail_package(self):
        return self._hail_package

    def utils_package_object(self):
        return self._utils_package_object

    def _rpc(self, action, payload) -> Tuple[bytes, str]:
        data = orjson.dumps(payload)
        path = action_routes[action]
        port = self._backend_server_port
        resp = self._requests_session.post(f'http://localhost:{port}{path}', data=data)
        if resp.status_code >= 400:
            error_json = orjson.loads(resp.content)
            raise fatal_error_from_java_error_triplet(error_json['short'], error_json['expanded'], error_json['error_id'])
        return resp.content, resp.headers.get('X-Hail-Timings', '')

    def stop(self):
        self._backend_server.stop()
        self._jbackend.close()
        self._jhc.stop()
        self._jhc = None
        self.sc.stop()
        self.sc = None
        self._registered_ir_function_names = set()
        uninstall_exception_handler()

    @property
    def logger(self):
        if self._logger is None:
            self._logger = Log4jLogger(self._utils_package_object)
        return self._logger

    @property
    def fs(self):
        if self._fs is None:
            self._fs = HadoopFS(self._utils_package_object, self._jbackend.fs())
        return self._fs

    def from_spark(self, df, key):
        result_tuple = self._jbackend.pyFromDF(df._jdf, key)
        tir_id, type_json = result_tuple._1(), result_tuple._2()
        return Table._from_java(ttable._from_json(orjson.loads(type_json)), tir_id)

    def to_spark(self, t, flatten):
        t = t.expand_types()
        if flatten:
            t = t.flatten()
        return pyspark.sql.DataFrame(self._jbackend.pyToDF(self._render_ir(t._tir)), self._spark_session)

    def register_ir_function(self, name, type_parameters, argument_names, argument_types, return_type, body):
        r = CSERenderer()
        assert not body._ir.uses_randomness
        code = r(body._ir)
        jbody = self._parse_value_ir(code, ref_map=dict(zip(argument_names, argument_types)))
        self._registered_ir_function_names.add(name)

        self.hail_package().expr.ir.functions.IRFunctionRegistry.pyRegisterIR(
            name,
            [ta._parsable_string() for ta in type_parameters],
            argument_names, [pt._parsable_string() for pt in argument_types],
            return_type._parsable_string(),
            jbody)

    def _is_registered_ir_function_name(self, name: str) -> bool:
        return name in self._registered_ir_function_names

    def read_multiple_matrix_tables(self, paths: 'List[str]', intervals: 'List[hl.Interval]', intervals_type):
        json_repr = {
            'paths': paths,
            'intervals': intervals_type._convert_to_json(intervals),
            'intervalPointType': intervals_type.element_type.point_type._parsable_string(),
        }

        results = self._jhc.backend().pyReadMultipleMatrixTables(json.dumps(json_repr))
        return [MatrixTable._from_java(jm) for jm in results]

    @property
    def requires_lowering(self):
        return False
