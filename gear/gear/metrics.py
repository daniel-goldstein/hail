import asyncio
from typing import List, Tuple, Any
from aiohttp import web
import prometheus_client as pc  # type: ignore
from prometheus_async.aio import time as prom_async_time  # type: ignore

import influxdb_client
from influxdb_client.client.write_api import ASYNCHRONOUS

INFLUXDB_BUCKET = 'default_bucket'
INFLUX_ORG = 'hail-vdc'
INFLUX_TOKEN = 'E1GwwGWeZ8RH9SCRLy7CFkLVyodWvraEvReXqXDUYJn9Z8ij1jsnYMT71m7MHMKgnozo8s5BH68jSCfpOZUy6A=='
INFLUX_URL = 'http://influxdb.dgoldste.svc.cluster.local:8086'


REQUEST_TIME = pc.Summary('http_request_latency_seconds', 'Endpoint latency in seconds', ['endpoint', 'verb'])
REQUEST_COUNT = pc.Counter('http_request_count', 'Number of HTTP requests', ['endpoint', 'verb', 'status'])
CONCURRENT_REQUESTS = pc.Gauge('http_concurrent_requests', 'Number of in progress HTTP requests', ['endpoint', 'verb'])


class InfluxClient:
    def __init__(self):
        self._client = client = influxdb_client.InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
        )
        self.write_api = client.write_api(write_options=ASYNCHRONOUS)

    def write(self, metric_name: str, labels: List[Tuple[str, str]], fields: List[Tuple[str, Any]]):
        p = influxdb_client.Point(metric_name)
        for label, val in labels:
            p = p.tag(label, val)
        for field, val in fields:
            p = p.field(field, val)

        self.write_api.write(bucket=INFLUXDB_BUCKET, record=p)


client = InfluxClient()


async def gauge(metric_name: str, tags: List[Tuple[str, str]], field_name: str, every=60):
    def create_report_loop(f):
        async def report_periodically():
            while True:
                val = await f()
                client.write(metric_name, tags, [(field_name, val)])
                asyncio.sleep(every)

        asyncio.ensure_future(report_periodically())
        return f

    return create_report_loop


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
