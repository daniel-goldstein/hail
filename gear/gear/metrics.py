import logging
import asyncio
from typing import List, Tuple, Any, Callable, Awaitable
from aiohttp import web
from hailtop import aiotools
from hailtop.config import DeployConfig
import prometheus_client as pc  # type: ignore
from prometheus_async.aio import time as prom_async_time  # type: ignore

import influxdb_client
from influxdb_client.client.flux_table import FluxTable
from influxdb_client.client.write_api import ASYNCHRONOUS


INFLUXDB_BUCKET = 'default_bucket'
INFLUX_ORG = 'hail-vdc'
INFLUX_TOKEN = 'E1GwwGWeZ8RH9SCRLy7CFkLVyodWvraEvReXqXDUYJn9Z8ij1jsnYMT71m7MHMKgnozo8s5BH68jSCfpOZUy6A=='


REQUEST_TIME = pc.Summary('http_request_latency_seconds', 'Endpoint latency in seconds', ['endpoint', 'verb'])
REQUEST_COUNT = pc.Counter('http_request_count', 'Number of HTTP requests', ['endpoint', 'verb', 'status'])
CONCURRENT_REQUESTS = pc.Gauge('http_concurrent_requests', 'Number of in progress HTTP requests', ['endpoint', 'verb'])

log = logging.getLogger('metrics')


class InfluxClient:
    def __init__(self, url):
        self._client = client = influxdb_client.InfluxDBClient(
            url=url,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            verify_ssl=False,
        )
        self.write_api = client.write_api(write_options=ASYNCHRONOUS)
        self.query_api = client.query_api()

    def write(self, points: List[influxdb_client.Point]):
        self.write_api.write(bucket=INFLUXDB_BUCKET, record=points)

    @classmethod
    def create_client(cls, deploy_config: DeployConfig) -> 'InfluxClient':
        return InfluxClient(deploy_config.base_url('influxdb'))

    def gauge(
        self,
        task_manager: aiotools.BackgroundTaskManager,
        f: Callable[[], Awaitable[List[influxdb_client.Point]]],
        every=60,
    ):
        async def report_periodically():
            while True:
                try:
                    points = await f()
                    self.write(points)
                except Exception as e:
                    log.exception(e)
                await asyncio.sleep(every)

        task_manager.ensure_future(report_periodically())

    def query(self, query) -> List[FluxTable]:
        return self.query_api.query(query)

    def close(self):
        self._client.close()


def make_point(metric_name: str, tags: List[Tuple[str, str]], fields: List[Tuple[str, Any]]):
    p = influxdb_client.Point(metric_name)
    for tag, val in tags:
        p = p.tag(tag, val)
    for field, val in fields:
        p = p.field(field, val)

    return p


@web.middleware
async def monitor_endpoints_middleware(request, handler):
    # Use the path template given to @route.<METHOD>, not the fully resolved one
    endpoint = request.match_info.route.resource.canonical
    verb = request.method
    CONCURRENT_REQUESTS.labels(endpoint=endpoint, verb=verb).inc()
    try:
        response = await prom_async_time(REQUEST_TIME.labels(endpoint=endpoint, verb=verb), handler(request))
        REQUEST_COUNT.labels(endpoint=endpoint, verb=verb, status=response.status).inc()
        return response
    except web.HTTPException as e:
        REQUEST_COUNT.labels(endpoint=endpoint, verb=verb, status=e.status).inc()
        raise e
    finally:
        CONCURRENT_REQUESTS.labels(endpoint=endpoint, verb=verb).dec()
